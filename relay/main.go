// Ретранслятор вебхуков Конспекта: готовая реализация договора CONTRACT.md.
//
// Принимает вебхуки CRM и АТС на публичный адрес, сразу запечатывает их
// открытым ключом компьютера с Конспектом (crypto_box_seal) и держит на
// диске, пока Конспект не заберёт их исходящим запросом. Тела вебхуков
// открытым текстом на диск и в журнал не попадают.
//
// Запуск:
//
//	relay                      — сервер (переменные окружения ниже)
//	relay pair                 — выдать новый код привязки и выйти
//
// Переменные окружения:
//
//	RELAY_DATA       каталог данных (по умолчанию ./data)
//	RELAY_ADDR       адрес сервера (по умолчанию :8080)
//	RELAY_DOMAIN     домен для сертификата Let's Encrypt; тогда слушаем :443 и :80
//	RELAY_PROXY      1 — работаем за своим nginx/caddy по HTTP, адрес клиента из X-Forwarded-For
//	RELAY_KEEP_DAYS  сколько дней хранить неподтверждённые события (по умолчанию 7)
package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/acme/autocert"
	"golang.org/x/crypto/nacl/box"
)

const (
	contractVersion = 1
	implName        = "konspekt-relay-go/1.0.0"
	maxBody         = 1 << 20 // 1 МБ
	maxEvents       = 10000   // на одного клиента
	maxWait         = 30 * time.Second
	hookRatePerSec  = 20
	pairFailLimit   = 10
	pairBlock       = 15 * time.Minute
)

var b64 = base64.RawURLEncoding

// --- состояние ---------------------------------------------------------------

type client struct {
	Name      string  `json:"name"`
	PublicKey string  `json:"public_key"`
	TokenHash string  `json:"token_hash"`
	Created   float64 `json:"created"`
}

type hook struct {
	ID         string            `json:"id"`
	Client     string            `json:"client"`
	Kind       string            `json:"kind"`
	SecretHash string            `json:"secret_hash"`
	Path       string            `json:"path"`
	Verify     map[string]string `json:"verify,omitempty"`
	Created    float64           `json:"created"`
}

type state struct {
	PairCodes []string           `json:"pair_codes"` // хеши неиспользованных кодов
	Clients   map[string]*client `json:"clients"`
	Hooks     map[string]*hook   `json:"hooks"`
}

type event struct {
	ID         string  `json:"id"`
	Hook       string  `json:"hook"`
	ReceivedAt float64 `json:"received_at"`
	Sealed     string  `json:"sealed"`
}

type Relay struct {
	dir      string
	keep     time.Duration
	proxy    bool
	mu       sync.Mutex
	st       state
	wake     map[string]chan struct{}
	limits   map[string]*bucket
	pairFail map[string]*failures
	now      func() time.Time
}

type bucket struct {
	tokens float64
	last   time.Time
}

type failures struct {
	count int
	until time.Time
}

func hash(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

func randomString(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return b64.EncodeToString(b)
}

// newID растёт по порядку прихода и сравнивается как строка.
func (r *Relay) newID() string {
	b := make([]byte, 5)
	_, _ = rand.Read(b)
	return fmt.Sprintf("%013d%s", r.now().UnixMilli(), hex.EncodeToString(b))
}

func NewRelay(dir string, keep time.Duration, proxy bool) (*Relay, error) {
	r := &Relay{
		dir: dir, keep: keep, proxy: proxy,
		wake: map[string]chan struct{}{}, limits: map[string]*bucket{},
		pairFail: map[string]*failures{}, now: time.Now,
	}
	if err := os.MkdirAll(filepath.Join(dir, "events"), 0o700); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(dir, "state.json"))
	switch {
	case errors.Is(err, os.ErrNotExist):
		r.st = state{Clients: map[string]*client{}, Hooks: map[string]*hook{}}
	case err != nil:
		return nil, err
	default:
		if err := json.Unmarshal(data, &r.st); err != nil {
			return nil, fmt.Errorf("state.json повреждён: %w", err)
		}
		if r.st.Clients == nil {
			r.st.Clients = map[string]*client{}
		}
		if r.st.Hooks == nil {
			r.st.Hooks = map[string]*hook{}
		}
	}
	return r, nil
}

// saveLocked записывает состояние атомарно: временный файл и переименование.
func (r *Relay) saveLocked() error {
	data, err := json.MarshalIndent(r.st, "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(r.dir, "state.json.tmp")
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(r.dir, "state.json"))
}

// NewPairCode выдаёт одноразовый код привязки. Хранится только его хеш.
func (r *Relay) NewPairCode() (string, error) {
	code := randomString(12)
	r.mu.Lock()
	defer r.mu.Unlock()
	r.st.PairCodes = append(r.st.PairCodes, hash(code))
	return code, r.saveLocked()
}

// --- HTTP ----------------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, status int, code string) {
	writeJSON(w, status, map[string]string{"error": code})
}

func (r *Relay) remoteIP(req *http.Request) string {
	if r.proxy {
		if f := req.Header.Get("X-Forwarded-For"); f != "" {
			return strings.TrimSpace(strings.Split(f, ",")[0])
		}
	}
	host, _, err := net.SplitHostPort(req.RemoteAddr)
	if err != nil {
		return req.RemoteAddr
	}
	return host
}

var hookPath = regexp.MustCompile(`^/h/[^/]+`)

// Журнал без тел и без секрета из пути приёмника.
func (r *Relay) logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		start := r.now()
		next.ServeHTTP(rec, req)
		path := hookPath.ReplaceAllString(req.URL.Path, "/h/<скрыт>")
		log.Printf("%s %s %d %s", req.Method, path, rec.status, r.now().Sub(start).Round(time.Millisecond))
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (r *Relay) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/health", r.health)
	mux.HandleFunc("POST /v1/pair", r.pair)
	mux.HandleFunc("POST /v1/hooks", r.auth(r.createHook))
	mux.HandleFunc("GET /v1/hooks", r.auth(r.listHooks))
	mux.HandleFunc("DELETE /v1/hooks/{id}", r.auth(r.deleteHook))
	mux.HandleFunc("GET /v1/events", r.auth(r.events))
	mux.HandleFunc("POST /v1/events/ack", r.auth(r.ack))
	mux.HandleFunc("/h/{secret}", r.receive)
	return r.logged(mux)
}

func (r *Relay) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, 200, map[string]any{"ok": true, "contract": contractVersion, "impl": implName})
}

type authed func(w http.ResponseWriter, req *http.Request, clientID string)

func (r *Relay) auth(next authed) http.HandlerFunc {
	return func(w http.ResponseWriter, req *http.Request) {
		token, ok := strings.CutPrefix(req.Header.Get("Authorization"), "Bearer ")
		if !ok || token == "" {
			fail(w, 401, "no_token")
			return
		}
		want := hash(token)
		r.mu.Lock()
		var found string
		for id, c := range r.st.Clients {
			if subtle.ConstantTimeCompare([]byte(c.TokenHash), []byte(want)) == 1 {
				found = id
			}
		}
		r.mu.Unlock()
		if found == "" {
			fail(w, 401, "no_token")
			return
		}
		next(w, req, found)
	}
}

func (r *Relay) pair(w http.ResponseWriter, req *http.Request) {
	ip := r.remoteIP(req)
	r.mu.Lock()
	if f := r.pairFail[ip]; f != nil && r.now().Before(f.until) {
		r.mu.Unlock()
		fail(w, 429, "slow_down")
		return
	}
	r.mu.Unlock()

	var body struct {
		Code      string `json:"code"`
		PublicKey string `json:"public_key"`
		Name      string `json:"name"`
	}
	if err := json.NewDecoder(io.LimitReader(req.Body, 4096)).Decode(&body); err != nil {
		fail(w, 400, "bad_request")
		return
	}
	key, err := b64.DecodeString(body.PublicKey)
	if err != nil || len(key) != 32 {
		fail(w, 400, "bad_key")
		return
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	codeHash := hash(body.Code)
	idx := -1
	for i, h := range r.st.PairCodes {
		if subtle.ConstantTimeCompare([]byte(h), []byte(codeHash)) == 1 {
			idx = i
		}
	}
	if body.Code == "" || idx < 0 {
		f := r.pairFail[ip]
		if f == nil {
			f = &failures{}
			r.pairFail[ip] = f
		}
		f.count++
		if f.count >= pairFailLimit {
			f.until = r.now().Add(pairBlock)
			f.count = 0
		}
		fail(w, 403, "bad_code")
		return
	}
	delete(r.pairFail, ip)
	r.st.PairCodes = append(r.st.PairCodes[:idx], r.st.PairCodes[idx+1:]...)
	id := randomString(9)
	token := randomString(32)
	name := body.Name
	if len(name) > 100 {
		name = name[:100]
	}
	r.st.Clients[id] = &client{Name: name, PublicKey: body.PublicKey, TokenHash: hash(token),
		Created: float64(r.now().Unix())}
	if err := r.saveLocked(); err != nil {
		fail(w, 500, "internal")
		return
	}
	writeJSON(w, 200, map[string]string{"client_id": id, "token": token})
}

var kinds = map[string]bool{"bitrix": true, "amocrm": true, "mango": true, "generic": true}

func (r *Relay) createHook(w http.ResponseWriter, req *http.Request, clientID string) {
	var body struct {
		Kind   string            `json:"kind"`
		Verify map[string]string `json:"verify"`
	}
	if err := json.NewDecoder(io.LimitReader(req.Body, 8192)).Decode(&body); err != nil || !kinds[body.Kind] {
		fail(w, 400, "bad_request")
		return
	}
	secret := randomString(32)
	h := &hook{ID: randomString(9), Client: clientID, Kind: body.Kind, SecretHash: hash(secret),
		Path: "/h/" + secret, Verify: body.Verify, Created: float64(r.now().Unix())}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.st.Hooks[h.ID] = h
	if err := r.saveLocked(); err != nil {
		fail(w, 500, "internal")
		return
	}
	writeJSON(w, 200, map[string]string{"id": h.ID, "path": h.Path})
}

func (r *Relay) listHooks(w http.ResponseWriter, _ *http.Request, clientID string) {
	r.mu.Lock()
	out := []map[string]any{}
	for _, h := range r.st.Hooks {
		if h.Client == clientID {
			out = append(out, map[string]any{"id": h.ID, "kind": h.Kind, "path": h.Path, "created_at": h.Created})
		}
	}
	r.mu.Unlock()
	sort.Slice(out, func(i, j int) bool { return out[i]["created_at"].(float64) < out[j]["created_at"].(float64) })
	writeJSON(w, 200, map[string]any{"hooks": out})
}

func (r *Relay) deleteHook(w http.ResponseWriter, req *http.Request, clientID string) {
	id := req.PathValue("id")
	r.mu.Lock()
	defer r.mu.Unlock()
	h, ok := r.st.Hooks[id]
	if !ok || h.Client != clientID {
		fail(w, 404, "not_found")
		return
	}
	delete(r.st.Hooks, id)
	if err := r.saveLocked(); err != nil {
		fail(w, 500, "internal")
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (r *Relay) allow(hookID string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := r.now()
	b := r.limits[hookID]
	if b == nil {
		b = &bucket{tokens: hookRatePerSec, last: now}
		r.limits[hookID] = b
	}
	b.tokens = min(hookRatePerSec, b.tokens+now.Sub(b.last).Seconds()*hookRatePerSec)
	b.last = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

// verify проверяет подлинность вебхука, если у приёмника это задано.
func verify(h *hook, req *http.Request, body []byte) bool {
	if len(h.Verify) == 0 {
		return true
	}
	same := func(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }
	switch h.Kind {
	case "bitrix":
		want := h.Verify["application_token"]
		if want == "" {
			return true
		}
		form, err := url.ParseQuery(string(body))
		return err == nil && same(form.Get("auth[application_token]"), want)
	default:
		name, value := h.Verify["secret_header"], h.Verify["secret_value"]
		if name == "" {
			return true
		}
		return same(req.Header.Get(name), value)
	}
}

func (r *Relay) receive(w http.ResponseWriter, req *http.Request) {
	if req.Method != http.MethodPost && req.Method != http.MethodGet {
		fail(w, 405, "bad_request")
		return
	}
	secretHash := hash(req.PathValue("secret"))
	r.mu.Lock()
	var h *hook
	for _, cand := range r.st.Hooks {
		if subtle.ConstantTimeCompare([]byte(cand.SecretHash), []byte(secretHash)) == 1 {
			h = cand
		}
	}
	var pub string
	if h != nil {
		if c := r.st.Clients[h.Client]; c != nil {
			pub = c.PublicKey
		}
	}
	r.mu.Unlock()
	if h == nil || pub == "" {
		fail(w, 404, "not_found")
		return
	}
	if !r.allow(h.ID) {
		fail(w, 429, "slow_down")
		return
	}
	body, err := io.ReadAll(io.LimitReader(req.Body, maxBody+1))
	if err != nil {
		fail(w, 400, "bad_request")
		return
	}
	if len(body) > maxBody {
		fail(w, 413, "too_large")
		return
	}
	if !verify(h, req, body) {
		fail(w, 403, "verify_failed")
		return
	}
	key, err := b64.DecodeString(pub)
	if err != nil || len(key) != 32 {
		fail(w, 500, "internal")
		return
	}
	var recipient [32]byte
	copy(recipient[:], key)
	received := float64(r.now().UnixNano()) / 1e9
	inner, _ := json.Marshal(map[string]any{
		"v": 1, "hook": h.ID, "kind": h.Kind, "received_at": received, "method": req.Method,
		"content_type": req.Header.Get("Content-Type"), "query": req.URL.RawQuery,
		"body": b64.EncodeToString(body),
	})
	sealed, err := box.SealAnonymous(nil, inner, &recipient, rand.Reader)
	if err != nil {
		fail(w, 500, "internal")
		return
	}
	ev := event{ID: r.newID(), Hook: h.ID, ReceivedAt: received, Sealed: b64.EncodeToString(sealed)}
	if err := r.store(h.Client, ev); err != nil {
		log.Printf("событие не сохранилось: %v", err)
		fail(w, 500, "internal")
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (r *Relay) clientDir(clientID string) string {
	return filepath.Join(r.dir, "events", clientID)
}

func (r *Relay) store(clientID string, ev event) error {
	dir := r.clientDir(clientID)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	data, _ := json.Marshal(ev)
	tmp := filepath.Join(dir, ev.ID+".tmp")
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(dir, ev.ID+".json")); err != nil {
		return err
	}
	r.trim(clientID)
	r.mu.Lock()
	ch := r.wake[clientID]
	delete(r.wake, clientID)
	r.mu.Unlock()
	if ch != nil {
		close(ch)
	}
	return nil
}

// ids — отсортированные id событий клиента.
func (r *Relay) ids(clientID string) []string {
	entries, _ := os.ReadDir(r.clientDir(clientID))
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		if name, ok := strings.CutSuffix(e.Name(), ".json"); ok {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// trim удаляет самые старые события сверх предела.
func (r *Relay) trim(clientID string) {
	ids := r.ids(clientID)
	for len(ids) > maxEvents {
		_ = os.Remove(filepath.Join(r.clientDir(clientID), ids[0]+".json"))
		ids = ids[1:]
	}
}

func (r *Relay) events(w http.ResponseWriter, req *http.Request, clientID string) {
	q := req.URL.Query()
	after := q.Get("after")
	limit, _ := strconv.Atoi(q.Get("limit"))
	if limit <= 0 || limit > 100 {
		limit = 50
	}
	waitSec, _ := strconv.Atoi(q.Get("wait"))
	wait := min(time.Duration(waitSec)*time.Second, maxWait)

	collect := func() []event {
		out := []event{}
		for _, id := range r.ids(clientID) {
			if id <= after {
				continue
			}
			data, err := os.ReadFile(filepath.Join(r.clientDir(clientID), id+".json"))
			if err != nil {
				continue
			}
			var ev event
			if json.Unmarshal(data, &ev) == nil {
				out = append(out, ev)
			}
			if len(out) >= limit {
				break
			}
		}
		return out
	}

	got := collect()
	if len(got) == 0 && wait > 0 {
		r.mu.Lock()
		ch := r.wake[clientID]
		if ch == nil {
			ch = make(chan struct{})
			r.wake[clientID] = ch
		}
		r.mu.Unlock()
		select {
		case <-ch:
		case <-time.After(wait):
		case <-req.Context().Done():
			return
		}
		got = collect()
	}
	writeJSON(w, 200, map[string]any{"events": got})
}

var safeID = regexp.MustCompile(`^[0-9a-f]+$`)

func (r *Relay) ack(w http.ResponseWriter, req *http.Request, clientID string) {
	var body struct {
		IDs []string `json:"ids"`
	}
	if err := json.NewDecoder(io.LimitReader(req.Body, 64*1024)).Decode(&body); err != nil {
		fail(w, 400, "bad_request")
		return
	}
	deleted := 0
	for _, id := range body.IDs {
		if !safeID.MatchString(id) {
			continue
		}
		if os.Remove(filepath.Join(r.clientDir(clientID), id+".json")) == nil {
			deleted++
		}
	}
	writeJSON(w, 200, map[string]any{"ok": true, "deleted": deleted})
}

// Expire удаляет неподтверждённые события старше срока хранения.
func (r *Relay) Expire() {
	cutoff := r.now().Add(-r.keep).UnixMilli()
	entries, _ := os.ReadDir(filepath.Join(r.dir, "events"))
	for _, c := range entries {
		for _, id := range r.ids(c.Name()) {
			ms, err := strconv.ParseInt(id[:min(13, len(id))], 10, 64)
			if err == nil && ms < cutoff {
				_ = os.Remove(filepath.Join(r.clientDir(c.Name()), id+".json"))
			}
		}
	}
}

// --- запуск ----------------------------------------------------------------------

func env(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

func main() {
	keepDays, _ := strconv.Atoi(env("RELAY_KEEP_DAYS", "7"))
	if keepDays <= 0 {
		keepDays = 7
	}
	relay, err := NewRelay(env("RELAY_DATA", "./data"), time.Duration(keepDays)*24*time.Hour, env("RELAY_PROXY", "") == "1")
	if err != nil {
		log.Fatal(err)
	}

	if len(os.Args) > 1 && os.Args[1] == "pair" {
		code, err := relay.NewPairCode()
		if err != nil {
			log.Fatal(err)
		}
		fmt.Println(code)
		return
	}

	if len(relay.st.Clients) == 0 && len(relay.st.PairCodes) == 0 {
		code, err := relay.NewPairCode()
		if err != nil {
			log.Fatal(err)
		}
		log.Printf("код привязки для Конспекта: %s (одноразовый; новый — командой `relay pair`)", code)
	}

	go func() {
		for range time.Tick(time.Hour) {
			relay.Expire()
		}
	}()

	srv := &http.Server{
		Handler:           relay.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      maxWait + 10*time.Second,
		IdleTimeout:       120 * time.Second,
	}

	if domain := os.Getenv("RELAY_DOMAIN"); domain != "" {
		m := &autocert.Manager{
			Prompt:     autocert.AcceptTOS,
			HostPolicy: autocert.HostWhitelist(domain),
			Cache:      autocert.DirCache(filepath.Join(relay.dir, "certs")),
		}
		go func() { log.Fatal(http.ListenAndServe(":80", m.HTTPHandler(nil))) }()
		srv.Addr = ":443"
		srv.TLSConfig = m.TLSConfig()
		log.Printf("ретранслятор: https://%s (договор %d)", domain, contractVersion)
		log.Fatal(srv.ListenAndServeTLS("", ""))
	}

	srv.Addr = env("RELAY_ADDR", ":8080")
	if !relay.proxy {
		log.Printf("ВНИМАНИЕ: без RELAY_DOMAIN и без RELAY_PROXY=1 ретранслятор работает по голому HTTP — только для проверки")
	}
	log.Printf("ретранслятор: %s (договор %d)", srv.Addr, contractVersion)
	log.Fatal(srv.ListenAndServe())
}
