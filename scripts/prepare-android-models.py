#!/usr/bin/env python3
"""Fetch the exact offline models; never accept an unverified model in an APK."""
import hashlib
import pathlib
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEST = ROOT / "android/app/src/main/assets/models"
REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
MODELS = {
    "face_detection_yunet/face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
}
ARCFACE_NAME = "w600k_r50.onnx"
ARCFACE_SHA256 = "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43"
BUFFALO_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
BUFFALO_SHA256 = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    for model, digest in MODELS.items():
        target = DEST / pathlib.Path(model).name
        if target.is_file() and checksum(target) == digest:
            print(f"Verified {target.name}")
            continue
        url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/models/{model}"
        temporary = target.with_suffix(".download")
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if checksum(temporary) != digest:
                raise RuntimeError(f"Model checksum mismatch: {model}")
            temporary.replace(target)
            print(f"Downloaded and verified {target.name}")
        finally:
            temporary.unlink(missing_ok=True)
    target = DEST / ARCFACE_NAME
    if target.is_file() and checksum(target) == ARCFACE_SHA256:
        print(f"Verified {ARCFACE_NAME}")
        return
    archive = DEST / "buffalo_l.download"
    temporary = target.with_suffix(".download")
    try:
        with urllib.request.urlopen(BUFFALO_URL, timeout=120) as response, archive.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        if checksum(archive) != BUFFALO_SHA256:
            raise RuntimeError("InsightFace model archive checksum mismatch")
        with zipfile.ZipFile(archive) as bundle, bundle.open(ARCFACE_NAME) as source, temporary.open("wb") as out:
            while chunk := source.read(1024 * 1024):
                out.write(chunk)
        if checksum(temporary) != ARCFACE_SHA256:
            raise RuntimeError("ArcFace model checksum mismatch")
        temporary.replace(target)
        print(f"Downloaded and verified {ARCFACE_NAME}")
    finally:
        archive.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    prepare()
