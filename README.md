# DocuMind ECM

**Permission-aware document intelligence for the enterprise: classify, extract, and ask questions over documents, with access control enforced at retrieval time.**

![Python](https://img.shields.io/badge/python-3.11+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-009688) ![ChromaDB](https://img.shields.io/badge/ChromaDB-vector%20store-orange) ![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF) ![License](https://img.shields.io/badge/license-MIT-green)

## Overview

Most "chat with your PDF" projects treat every document as equally visible. In a real enterprise that is a data-leak risk: an employee should never receive an answer derived from a confidential HR or finance document.

DocuMind ECM brings Enterprise Content Management practices (capture, classification, metadata, ACLs) into a modern LLM pipeline. Documents are ingested, automatically classified, enriched with structured metadata, and made searchable through a retrieval-augmented (RAG) assistant that **only retrieves what the asking user is allowed to read**.

> The author is an ECM practitioner (OpenText xECM, Documentum, Captiva), and the design maps those concepts directly onto the LLM stack.

## Key features

- **Ingestion**: PDF / TXT / MD text extraction, page-aware chunking with overlap.
- **Auto-classification**: LLM labels each document (invoice, contract, policy, resume, report, letter, other).
- **Metadata extraction**: structured fields (e.g. invoice number, vendor, total) stored as JSON, similar to object attributes in Documentum.
- **Permission-aware RAG**: role-to-access-level filtering is applied *inside the vector query*, so restricted chunks never reach the LLM prompt.
- **Cited answers**: every answer references source file and page.
- **Graceful degradation**: if classification fails, ingestion still succeeds.
- **Engineering hygiene**: typed API, Docker, tests with the LLM mocked, lint + test CI.

## ECM concepts mapped to this project

| ECM concept (Captiva / Documentum / xECM) | DocuMind ECM |
|---|---|
| Capture | `app/ingest.py`: extraction and chunking (OCR hook on roadmap) |
| Document classification | `app/classify.py` |
| Object type and attributes | SQLite metadata store (`app/db.py`) |
| ACL / security levels | `app/security.py` + filtered vector query |
| Search and retrieval | ChromaDB + cited RAG (`app/rag.py`) |

## Architecture

```
 Streamlit UI ──► FastAPI ──► Ingest (extract → chunk)
                    │              ├─► Classify + extract fields (LLM → JSON)
                    │              ├─► SQLite   (document metadata)
                    │              └─► ChromaDB (chunks + access_level)
                    │
                    └─► /ask → role → allowed levels → filtered retrieval → LLM → answer + citations
```

## Access model

| Role | Can read |
|---|---|
| employee | public |
| manager | public, internal |
| admin | public, internal, confidential |

## Demo

Upload the files in `samples/`, using the access level in each filename, then ask:

> *"What salary band do senior engineers get?"*

- As **employee**: the assistant reports it could not find the answer.
- As **admin**: it answers and cites `salary_policy_confidential.txt`.

Same question, different result, enforced before the LLM ever sees the text.

## Quick start

```bash
git clone https://github.com/<your-username>/documind-ecm.git
cd documind-ecm
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload                       # API docs: http://localhost:8000/docs
streamlit run frontend/streamlit_app.py             # UI:       http://localhost:8501
```

Or: `docker compose up --build`

## API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/documents` | Upload a file with `access_level` (public / internal / confidential) |
| GET | `/documents?role=` | List documents visible to a role, with type and extracted fields |
| POST | `/ask` | `{question, role}` returns an answer plus sources |

## Tests

```bash
pytest -q
ruff check .
```

Covers role permissions, chunking, JSON parsing and fallback, and API-level role filtering. The LLM is mocked, so no API key is needed. CI runs on every push.

## Design decisions

- **Filter before retrieval, not after.** Post-filtering still lets restricted text influence ranking and risks prompt leakage; a metadata filter in the query does not.
- **Classification is best-effort.** An LLM failure must not block document capture.
- **Simple stores first.** SQLite and a local ChromaDB keep setup to one command; the interfaces are isolated for a later swap to PostgreSQL + pgvector.

## Limitations

- Roles are passed by the client; there is no real authentication yet (JWT is on the roadmap).
- Scanned PDFs without a text layer are rejected until OCR is added.
- No evaluation of answer quality yet.

## Roadmap

- [ ] OCR for scanned documents (Tesseract)
- [ ] JWT authentication and per-user ACLs
- [ ] Evaluation harness (retrieval hit-rate, answer faithfulness)
- [ ] PostgreSQL + pgvector
- [ ] SharePoint / Documentum REST connector
- [ ] Cloud deployment

## License

MIT
