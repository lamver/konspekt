"""Голоса: обрывки не плодят участников, разошедшиеся эталоны сливаются.

Созвон 21.10 на двоих дал 35 «Голосов»: 26 из них с одной фразой короче
двух секунд, а пять крупных были двумя людьми, которых в начале встречи
разнесло. Здесь это воспроизводится на искусственных отпечатках: у
каждого человека своё направление, фраза это направление плюс шум, и у
первых фраз шум побольше, как у голоса через динамик.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import numpy as np

from app.asr import voices as v

rng = np.random.default_rng(7)
DIM = 64


def person() -> np.ndarray:
    x = rng.normal(size=DIM).astype(np.float32)
    return x / np.linalg.norm(x)


def phrase(base: np.ndarray, noise: float) -> np.ndarray:
    x = base + rng.normal(size=DIM).astype(np.float32) * noise
    return x / np.linalg.norm(x)


def main() -> int:
    anna, boris = person(), person()

    # --- короткий обрывок не заводит участника
    r = v.VoiceRoster()
    first = r.assign("them", phrase(anna, 0.05), seconds=4.0)
    assert first is not None and first.label == "Собеседник 1"
    junk = r.assign("them", phrase(boris, 0.2), seconds=0.8)
    assert junk is None, "обрывок чужого голоса завёл участника"
    assert len(r.voices()) == 1
    short_same = r.assign("them", phrase(anna, 0.05), seconds=1.0)
    assert short_same is first, "короткая фраза знакомого голоса должна примкнуть к нему"
    print("[ok] обрывок чужого голоса не заводит участника, свой примыкает")

    # --- один человек, которого разнесло на двоих, сливается.
    # Два «окраса» одного голоса: через динамик и через микрофон. В
    # начале окрас сильный, эталоны расходятся; по ходу встречи он
    # слабеет, эталоны сходятся к самому голосу.
    d1, d2 = person(), person()
    r = v.VoiceRoster()
    for _ in range(5):
        r.assign("them", phrase(anna + 1.3 * d1, 0.05), seconds=3.0)
    for _ in range(5):
        r.assign("them", phrase(anna + 1.3 * d2, 0.05), seconds=3.0)
    b = r.assign("them", phrase(boris, 0.05), seconds=3.0)
    assert len(r.voices()) == 3, f"ожидали две ветки Анны и Бориса, а участников {len(r.voices())}"
    last = None
    for k in range(50):
        s = 1.3 * max(0.15, 1 - k / 10)
        окрас = d1 if k % 2 == 0 else d2
        last = r.assign("them", phrase(anna + s * окрас, 0.05), seconds=3.0)
    merged = r.забрать_слияния()
    survivors = r.voices()
    anna_voices = [x for x in survivors if x.id != b.id]
    assert len(anna_voices) == 1, (
        f"Анну должно было собрать в одного участника, а их {len(anna_voices)}: "
        f"{[x.label for x in survivors]}"
    )
    assert b.id in {x.id for x in survivors}, "Бориса слили с Анной"
    assert merged, "слияние было, а сервису о нём не сказали"
    старый, в_кого = merged[-1]
    assert в_кого is anna_voices[0] and last is anna_voices[0]
    print(f"[ok] две ветки одного голоса слились: {старый} → «{в_кого.label}», Борис остался")

    # --- после слияния подписи не повторяются
    r = v.VoiceRoster()
    r.assign("them", phrase(anna, 0.1), seconds=3.0)
    r.assign("them", phrase(boris, 0.1), seconds=3.0)
    r._voices.pop(next(iter(r._voices)))  # будто кого-то слили
    third = r.assign("them", phrase(person(), 0.1), seconds=3.0)
    assert third.label == "Собеседник 3", f"подпись повторилась: {third.label}"
    print("[ok] подписи нумеруются по счётчику, после слияния не повторяются")

    # --- без длительности поведение прежнее
    r = v.VoiceRoster()
    assert r.assign("me", phrase(anna, 0.1)).label == "Я"
    assert r.assign("me", phrase(boris, 0.1)).label == "Голос 2"
    print("[ok] без длительности фраза заводит участника, как раньше")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
