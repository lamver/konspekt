"""Проверка целостности всех скачанных моделей.

Дважды запущенная загрузка дописывала в один и тот же .part, и файл
выходил больше настоящего. Проверяем размеры по серверу и то, что модели
действительно открываются.
"""
import urllib.request

import onnxruntime as ort

from app.core import paths

MODELS = {
    "gigaam-v3-e2e": ("istupakov/gigaam-v3-onnx",
                      ["v3_e2e_ctc.int8.onnx", "v3_e2e_ctc.yaml",
                       "v3_e2e_ctc_vocab.txt", "config.json"]),
    "gigaam-v3-ctc": ("istupakov/gigaam-v3-onnx",
                      ["v3_ctc.int8.onnx", "v3_ctc.yaml", "v3_vocab.txt", "config.json"]),
    "wespeaker": ("Wespeaker/wespeaker-voxceleb-resnet34-LM",
                  ["voxceleb_resnet34_LM.onnx"]),
    "voxlingua": ("beginning-ai/speechbrain-lang-id-voxlingua107-ecapa-onnx",
                  ["model.onnx"]),
}

root = paths.models_dir()
bad = []
for folder, (repo, files) in MODELS.items():
    for name in files:
        path = root / folder / name
        if not path.exists():
            print(f"[--] {folder}/{name}: нет на диске")
            continue
        local = path.stat().st_size
        try:
            req = urllib.request.Request(
                f"https://huggingface.co/{repo}/resolve/main/{name}",
                method="HEAD", headers={"User-Agent": "konspekt"})
            remote = int(urllib.request.urlopen(req, timeout=60)
                         .headers.get("Content-Length") or 0)
        except Exception as e:
            print(f"[??] {folder}/{name}: сервер недоступен ({e})")
            continue
        ok = local == remote
        mark = "ok" if ok else "!!"
        print(f"[{mark}] {folder}/{name}: {local} байт"
              + ("" if ok else f", а на сервере {remote} (разница {local-remote:+d})"))
        if not ok:
            bad.append(f"{folder}/{name}")

print("\nпроверяем, что модели открываются:")
for folder, (_, files) in MODELS.items():
    for name in files:
        if not name.endswith(".onnx"):
            continue
        path = root / folder / name
        if not path.exists():
            continue
        try:
            ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            print(f"[ok] {folder}/{name} открывается")
        except Exception as e:
            print(f"[!!] {folder}/{name}: {str(e)[:90]}")
            bad.append(f"{folder}/{name}")

assert not bad, f"повреждены: {sorted(set(bad))}"
print("\nВсе модели целы.")
