# SyncNotes

A collaborative note-taking system, built as a project for the Distributed
Software Systems course (A.A. 2025/2026). Shared plain-text notes live on a
cluster of three Raft-replicated server nodes; clients never talk to a
replica directly, only to a single gateway that hides which one is leader.

## Getting started

Requirements: Docker Engine 20.10+, Docker Compose v2, Python 3.12+.

```bash
git clone https://github.com/marziademaina/SyncNotes.git
cd SyncNotes

docker compose up --build -d     # bring up the 3-replica cluster + gateway

python3 -m venv venv && source venv/bin/activate
pip install -r client/requirements.txt
python3 syncnotes.py             # run the client
```
