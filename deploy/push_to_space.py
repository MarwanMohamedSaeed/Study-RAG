"""Deploy the StudyRAG demo to a Hugging Face Space (Docker SDK, free CPU hardware).

    python deploy/push_to_space.py --space <hf-username>/studyrag --dry-run   # show what would be uploaded
    python deploy/push_to_space.py --space <hf-username>/studyrag             # create/update the Space

Needs in .env (never committed):
  HF_TOKEN       a Hugging Face access token with *write* permission (https://huggingface.co/settings/tokens)
  GROQ_API_KEY   stored as a Space *secret*, so it never appears in the uploaded files
  REPO_URL       optional: your GitHub link, shown in the demo banner
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config  # noqa: E402  (loads .env)

INCLUDE = ["app.py", "requirements.txt", "LICENSE", ".streamlit/config.toml", "core", "views",
           "samples/make_sample_pdf.py", "samples/make_benchmark_corpus.py", "samples/networks_lecture.pdf",
           "samples/network_layer_lecture.pdf", "samples/routing_slides.pptx"]
SPACE_FILES = {"deploy/huggingface/Dockerfile": "Dockerfile", "deploy/huggingface/README.md": "README.md"}
NEVER = {".env", "data", "models", ".venv", "__pycache__"}


def stage(dest: Path) -> list[Path]:
    for item in INCLUDE:
        src = ROOT / item
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dest / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            (dest / item).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / item)
    for src, name in SPACE_FILES.items():
        shutil.copy2(ROOT / src, dest / name)
    files = sorted(p for p in dest.rglob("*") if p.is_file())
    leaked = [p for p in files if NEVER & set(p.relative_to(dest).parts)]
    if leaked:
        raise SystemExit(f"Refusing to upload private files: {leaked}")
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        files = stage(dest)
        total = sum(p.stat().st_size for p in files)
        print(f"{len(files)} files, {total / 1e6:.1f} MB to upload to spaces/{args.space}:")
        for p in files:
            print(f"  {p.relative_to(dest).as_posix()}")
        if args.dry_run:
            print("\nDry run: nothing uploaded.")
            return

        token = os.getenv("HF_TOKEN", "")
        if not token:
            raise SystemExit("Set HF_TOKEN (a write token from https://huggingface.co/settings/tokens) in .env")
        if not config.GROQ_API_KEY:
            raise SystemExit("Set GROQ_API_KEY in .env: the demo's LLM runs on Groq's free API")
        from huggingface_hub import HfApi
        api = HfApi(token=token)
        api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)
        api.add_space_secret(args.space, "GROQ_API_KEY", config.GROQ_API_KEY)   # secret: not in any file
        if config.REPO_URL:
            api.add_space_variable(args.space, "REPO_URL", config.REPO_URL)
        api.upload_folder(folder_path=str(dest), repo_id=args.space, repo_type="space",
                          commit_message="Deploy StudyRAG demo")
        print(f"\nDeployed. The Space builds in ~5-10 minutes: https://huggingface.co/spaces/{args.space}")


if __name__ == "__main__":
    main()
