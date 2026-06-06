# Face Manager – GPU-beschleunigte Gesichtserkennung & Clusterung

Dieses Projekt bietet:

- RetinaFace Face Detection (GPU)
- ArcFace Embeddings (512-D)
- DBSCAN Clusterung
- HNSWlib Ähnlichkeitssuche
- Moderne Web-UI (React + Vite)
- WSL2 + NVIDIA GPU Support
- Keine Bildduplikate – nur Pfade werden gespeichert

---

## Installation (WSL2)

### 1. Python venv

```bash
sudo apt update
sudo apt install python3-venv python3-dev build-essential

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
