package main

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"golang.org/x/crypto/nacl/box"
)

type stand struct {
	t      *testing.T
	relay  *Relay
	srv    *httptest.Server
	pub    *[32]byte
	priv   *[32]byte
	token  string
	client string
}

func newStand(t *testing.T) *stand {
	t.Helper()
	r, err := NewRelay(t.TempDir(), 7*24*time.Hour, false)
	if err != nil {
		t.Fatal(err)
	}
	pub, priv, err := box.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	s := &stand{t: t, relay: r, srv: httptest.NewServer(r.Handler()), pub: pub, priv: priv}
	t.Cleanup(s.srv.Close)
	return s
}

func (s *stand) do(method, path, token string, body any) (int, map[string]any) {
	s.t.Helper()
	var rd io.Reader
	if body != nil {
		data, _ := json.Marshal(body)
		rd = bytes.NewReader(data)
	}
	req, _ := http.NewRequest(method, s.srv.URL+path, rd)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		s.t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func (s *stand) pair() {
	s.t.Helper()
	code, err := s.relay.NewPairCode()
	if err != nil {
		s.t.Fatal(err)
	}
	st, out := s.do("POST", "/v1/pair", "", map[string]string{
		"code": code, "public_key": b64.EncodeToString(s.pub[:]), "name": "Ноутбук"})
	if st != 200 {
		s.t.Fatalf("привязка: %d %v", st, out)
	}
	s.token, s.client = out["token"].(string), out["client_id"].(string)
	// Код одноразовый.
	if st, _ := s.do("POST", "/v1/pair", "", map[string]string{
		"code": code, "public_key": b64.EncodeToString(s.pub[:])}); st != 403 {
		s.t.Fatalf("повторный код принят: %d", st)
	}
}

func (s *stand) hook(kind string, verify map[string]string) string {
	s.t.Helper()
	st, out := s.do("POST", "/v1/hooks", s.token, map[string]any{"kind": kind, "verify": verify})
	if st != 200 {
		s.t.Fatalf("приёмник: %d %v", st, out)
	}
	return out["path"].(string)
}

func (s *stand) post(path, contentType, body string) int {
	s.t.Helper()
	resp, err := http.Post(s.srv.URL+path, contentType, strings.NewReader(body))
	if err != nil {
		s.t.Fatal(err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

func (s *stand) open(sealed string) map[string]any {
	s.t.Helper()
	data, err := b64.DecodeString(sealed)
	if err != nil {
		s.t.Fatal(err)
	}
	plain, ok := box.OpenAnonymous(nil, data, s.pub, s.priv)
	if !ok {
		s.t.Fatal("конверт не вскрылся ключом клиента")
	}
	out := map[string]any{}
	if err := json.Unmarshal(plain, &out); err != nil {
		s.t.Fatal(err)
	}
	return out
}

func (s *stand) events(query string) []map[string]any {
	s.t.Helper()
	st, out := s.do("GET", "/v1/events"+query, s.token, nil)
	if st != 200 {
		s.t.Fatalf("события: %d", st)
	}
	list := []map[string]any{}
	for _, e := range out["events"].([]any) {
		list = append(list, e.(map[string]any))
	}
	return list
}

func TestHealth(t *testing.T) {
	s := newStand(t)
	st, out := s.do("GET", "/v1/health", "", nil)
	if st != 200 || out["contract"].(float64) != 1 {
		t.Fatalf("health: %d %v", st, out)
	}
}

func TestPairing(t *testing.T) {
	s := newStand(t)
	if st, _ := s.do("POST", "/v1/pair", "", map[string]string{"code": "x", "public_key": "short"}); st != 400 {
		t.Fatalf("короткий ключ принят: %d", st)
	}
	s.pair()
	if st, _ := s.do("GET", "/v1/hooks", "", nil); st != 401 {
		t.Fatalf("без токена пустили: %d", st)
	}
	if st, _ := s.do("GET", "/v1/hooks", "чужой", nil); st != 401 {
		t.Fatalf("с чужим токеном пустили: %d", st)
	}
	// Токен хранится хешем, а не как есть.
	for _, c := range s.relay.st.Clients {
		if c.TokenHash == s.token || strings.Contains(c.TokenHash, s.token) {
			t.Fatal("токен лежит в состоянии открытым")
		}
	}
}

func TestPairBruteForceBlocked(t *testing.T) {
	s := newStand(t)
	code, _ := s.relay.NewPairCode()
	key := b64.EncodeToString(s.pub[:])
	for i := 0; i < pairFailLimit; i++ {
		s.do("POST", "/v1/pair", "", map[string]string{"code": "неверно", "public_key": key})
	}
	if st, _ := s.do("POST", "/v1/pair", "", map[string]string{"code": code, "public_key": key}); st != 429 {
		t.Fatalf("после 10 неудач верный код всё равно принят: %d", st)
	}
}

func TestReceiveSealPullAck(t *testing.T) {
	s := newStand(t)
	s.pair()
	path := s.hook("bitrix", map[string]string{"application_token": "APP"})
	if len(strings.TrimPrefix(path, "/h/")) < 40 {
		t.Fatalf("секрет в пути короткий: %s", path)
	}

	form := url.Values{"event": {"ONVOXIMPLANTCALLEND"}, "data[CALL_ID]": {"A1"}, "auth[application_token]": {"APP"}}
	if st := s.post(path, "application/x-www-form-urlencoded", form.Encode()); st != 200 {
		t.Fatalf("вебхук не принят: %d", st)
	}
	form.Set("auth[application_token]", "ЧУЖОЙ")
	if st := s.post(path, "application/x-www-form-urlencoded", form.Encode()); st != 403 {
		t.Fatalf("вебхук с чужим токеном приложения принят: %d", st)
	}
	if st := s.post("/h/несуществующий", "application/json", "{}"); st != 404 {
		t.Fatalf("неизвестный приёмник: %d", st)
	}
	if st := s.post(path, "application/json", strings.Repeat("x", maxBody+10)); st != 413 {
		t.Fatalf("тело больше 1 МБ принято: %d", st)
	}

	evs := s.events("")
	if len(evs) != 1 {
		t.Fatalf("ждали одно событие, пришло %d", len(evs))
	}
	inner := s.open(evs[0]["sealed"].(string))
	body, _ := b64.DecodeString(inner["body"].(string))
	if inner["kind"] != "bitrix" || !strings.Contains(string(body), "data%5BCALL_ID%5D=A1") {
		t.Fatalf("внутри конверта не то: %v %s", inner, body)
	}

	// На диске нет тела открытым текстом.
	raw, _ := io.ReadAll(strings.NewReader(mustRead(t, s.relay.clientDir(s.client))))
	if strings.Contains(string(raw), "ONVOXIMPLANTCALLEND") {
		t.Fatal("тело вебхука лежит на диске открытым")
	}

	if got := s.events("?after=" + evs[0]["id"].(string)); len(got) != 0 {
		t.Fatalf("after не отсекает полученное: %d", len(got))
	}
	st, out := s.do("POST", "/v1/events/ack", s.token, map[string]any{"ids": []string{evs[0]["id"].(string)}})
	if st != 200 || out["deleted"].(float64) != 1 {
		t.Fatalf("подтверждение: %d %v", st, out)
	}
	if len(s.events("")) != 0 {
		t.Fatal("подтверждённое событие не удалено")
	}
}

func mustRead(t *testing.T, dir string) string {
	t.Helper()
	var all strings.Builder
	entries, _ := osReadDir(dir)
	for _, e := range entries {
		all.WriteString(readFile(dir, e))
	}
	return all.String()
}

func TestLongPollWakes(t *testing.T) {
	s := newStand(t)
	s.pair()
	path := s.hook("generic", nil)
	done := make(chan []map[string]any)
	go func() { done <- s.events("?wait=10") }()
	time.Sleep(200 * time.Millisecond)
	start := time.Now()
	s.post(path, "application/json", `{"call":"x"}`)
	select {
	case evs := <-done:
		if len(evs) != 1 {
			t.Fatalf("долгий опрос вернул %d событий", len(evs))
		}
		if time.Since(start) > 3*time.Second {
			t.Fatal("долгий опрос проснулся не сразу")
		}
	case <-time.After(8 * time.Second):
		t.Fatal("долгий опрос не проснулся от нового события")
	}
}

func TestOtherClientCannotSeeEvents(t *testing.T) {
	s := newStand(t)
	s.pair()
	path := s.hook("generic", nil)
	s.post(path, "application/json", `{}`)
	other := &stand{t: t, relay: s.relay, srv: s.srv}
	other.pub, other.priv, _ = box.GenerateKey(rand.Reader)
	other.pair()
	if len(other.events("")) != 0 {
		t.Fatal("второй клиент видит чужие события")
	}
	if st, _ := other.do("GET", "/v1/hooks", other.token, nil); st != 200 {
		t.Fatal("список приёмников")
	}
	id := ""
	for k := range s.relay.st.Hooks {
		id = k
	}
	if st, _ := other.do("DELETE", "/v1/hooks/"+id, other.token, nil); st != 404 {
		t.Fatalf("чужой клиент удалил приёмник: %d", st)
	}
}

func TestExpireAndDeleteHook(t *testing.T) {
	s := newStand(t)
	s.pair()
	path := s.hook("generic", nil)
	s.post(path, "application/json", `{}`)
	s.relay.now = func() time.Time { return time.Now().Add(8 * 24 * time.Hour) }
	s.relay.Expire()
	s.relay.now = time.Now
	if len(s.events("")) != 0 {
		t.Fatal("событие старше 7 дней не удалено")
	}
	_, out := s.do("GET", "/v1/hooks", s.token, nil)
	id := out["hooks"].([]any)[0].(map[string]any)["id"].(string)
	if st, _ := s.do("DELETE", "/v1/hooks/"+id, s.token, nil); st != 200 {
		t.Fatal("удаление приёмника")
	}
	if st := s.post(path, "application/json", `{}`); st != 404 {
		t.Fatalf("удалённый приёмник принимает: %d", st)
	}
}

func TestLogHidesSecret(t *testing.T) {
	var buf bytes.Buffer
	log.SetOutput(&buf)
	defer log.SetOutput(io.Discard)
	s := newStand(t)
	s.pair()
	path := s.hook("generic", nil)
	s.post(path, "application/json", `{"phone":"+79161234567"}`)
	text := buf.String()
	if strings.Contains(text, strings.TrimPrefix(path, "/h/")) || strings.Contains(text, "79161234567") {
		t.Fatalf("секрет пути или тело попали в журнал:\n%s", text)
	}
	if !strings.Contains(text, "/h/<скрыт>") {
		t.Fatalf("приёмник не виден в журнале вовсе:\n%s", text)
	}
}

func TestStateSurvivesRestart(t *testing.T) {
	s := newStand(t)
	s.pair()
	path := s.hook("generic", nil)
	again, err := NewRelay(s.relay.dir, 7*24*time.Hour, false)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(again.Handler())
	defer srv.Close()
	resp, err := http.Post(srv.URL+path, "application/json", strings.NewReader(`{}`))
	if err != nil || resp.StatusCode != 200 {
		t.Fatalf("после перезапуска приёмник не работает: %v %v", err, resp)
	}
}

func osReadDir(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	out := []string{}
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out, err
}

func readFile(dir, name string) string {
	data, _ := os.ReadFile(filepath.Join(dir, name))
	return string(data)
}
