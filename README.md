# Atlas — Multi-Document GraphRAG Studio

**An evidence-backed question-answering platform powered by Graph Retrieval-Augmented Generation (GraphRAG).**

Atlas transforms multiple documents into a connected knowledge system, allowing users to explore relationships, retrieve relevant evidence, and answer complex questions with page-level citations and explainable graph paths.

It combines document intelligence, knowledge graph construction, hybrid retrieval, and LLM-powered answer generation in a unified web application.

---

## Overview

Traditional RAG systems primarily retrieve information based on semantic similarity. Atlas extends this approach by combining vector retrieval, keyword search, and knowledge graph traversal.

This enables Atlas to:

- Process and organize information from multiple documents.
- Identify entities, relationships, and important claims.
- Connect related information across documents.
- Retrieve relevant evidence using multiple retrieval strategies.
- Support multi-hop question answering.
- Generate evidence-backed answers with page references.
- Explore knowledge graphs, themes, claims, and relationships through an interactive interface.

Atlas includes an offline extractive answering mode as well as an optional Groq-powered LLM mode.

---

## Key Features

### 1. Multi-Document Intelligence
- Upload PDF, DOCX, and TXT documents.
- Extract and process document content.
- Generate page-aware chunks.
- Track document indexing status.
- Detect duplicate documents.
- Archive, restore, delete, and reindex documents.

### 2. Knowledge Graph Construction
- Extract entities and relationships.
- Identify aliases and acronyms.
- Resolve duplicate entities.
- Connect related information across documents.
- Preserve evidence provenance.
- Explore relationships through graph visualizations.

### 3. Hybrid Retrieval Engine
Combines multiple retrieval strategies:

- Vector similarity search.
- SQLite FTS5 keyword retrieval (BM25).
- Knowledge graph retrieval.
- Reciprocal Rank Fusion (RRF).
- Feature-based reranking.
- Maximum Marginal Relevance (MMR).
- Optional cross-encoder reranking.

### 4. Multi-Hop Question Answering
- Retrieve related evidence across multiple documents.
- Follow relationships between entities.
- Build explainable evidence paths.
- Generate answers grounded in retrieved passages.
- Display supporting page references.

### 5. Community Detection and Global Search
- Detect knowledge communities using Louvain clustering.
- Organize communities into two levels.
- Generate theme reports.
- Support broader questions across document collections.

### 6. Claims and Contradiction Detection
- Extract important claims from documents.
- Identify numeric conflicts.
- Detect exclusive-value conflicts.
- Identify negation-based contradictions.
- Provide a review workflow for conflicting claims.

### 7. Observability and Evaluation
- Track retrieval and generation traces.
- Monitor p50, p95, and p99 latency.
- Track token usage and estimated costs.
- Monitor answer grounding.
- Expose Prometheus-compatible metrics.
- Evaluate different retrieval strategies.

### 8. Security and Multi-User Support
- Admin, editor, and viewer roles.
- Private and shared documents.
- CSRF protection.
- Rate limiting.
- Login lockout.
- Content Security Policy.
- Upload validation.
- Prompt-injection neutralization.
- Audit logging.
- User feedback through ratings, tags, and comments.

---

## Architecture

Atlas uses a modular architecture that separates the web interface, API, application engine, and original GraphRAG pipeline.

```text
                   USER
                     |
                     v
             ATLAS WEB INTERFACE
                  (web/)
                     |
                     v
               FLASK API
                (server/)
                     |
                     v
              ATLAS ENGINE
                (studio/)
                     |
          +----------+----------+
          |          |          |
          v          v          v
       INGESTION  RETRIEVAL   GRAPH
          |          |          |
          v          v          v
       CHUNKING   HYBRID     ENTITY
          |       SEARCH    RESOLUTION
          v          |          |
       EMBEDDINGS    v          v
          |       RERANKING  GRAPH PATHS
          |          |          |
          +----------+----------+
                     |
                     v
              ANSWER GENERATION
                     |
             +-------+-------+
             |               |
             v               v
          GROQ LLM       OFFLINE MODE
             |               |
             +-------+-------+
                     |
                     v
            EVIDENCE-BACKED
                 ANSWER
```

### Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python |
| Backend API | Flask |
| Frontend | HTML, CSS, JavaScript |
| Knowledge Graph | Neo4j pipeline and graph engine |
| Local Application Storage | SQLite |
| Vector Embeddings | Sentence Transformers / hashed embeddings |
| Keyword Retrieval | SQLite FTS5 |
| LLM Integration | Groq API |
| PDF Processing | Python document-processing libraries |
| Evaluation | Custom evaluation framework |
| Visualization | JavaScript Canvas and SVG |

---

## Project Structure

```text
Atlas/
│
├── server/
│   ├── app.py
│   └── security.py
│
├── studio/
│   ├── engine.py
│   ├── pipeline.py
│   ├── retrieval.py
│   ├── graphsearch.py
│   ├── resolution.py
│   ├── communities.py
│   ├── claims.py
│   ├── evaluation.py
│   └── observability.py
│
├── src/
│   └── Original Neo4j + Groq GraphRAG pipeline
│
├── web/
│   ├── HTML files
│   ├── CSS files
│   └── JavaScript modules
│
├── data/
│   └── Knowledge-base text files
│
├── samples/
│   └── Sample demonstration PDFs
│
├── scripts/
│   └── Utility scripts
│
├── evaluation/
│
├── tests/
│
├── uploads/
│
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Installation and Setup

### Prerequisites

- Python 3.11 or later.
- pip.
- Git.
- A Groq API key for LLM-powered functionality (optional for offline mode).

### 1. Clone the repository

```bash
git clone https://github.com/R-Karthik7/atlas-GraphRAG.git

cd atlas-graphrag-studio
```

### 2. Create a virtual environment

macOS / Linux:

```bash
python3 -m venv .venv

source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv

.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root.

Use `.env.example` as the reference for supported variables.

Example:

```env
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b

NEO4J_URI=your_neo4j_uri
NEO4J_USERNAME=your_neo4j_username
NEO4J_PASSWORD=your_neo4j_password
```

Additional optional settings are documented in `.env.example`.

**Important:** Never commit your `.env` file, API keys, passwords, or private documents to GitHub.

---

## Running Atlas

### Offline Mode

Run Atlas without requiring an API key:

```bash
python server/app.py --offline
```

Open:

```text
http://localhost:8000
```

Offline mode uses extractive answer generation, rule-based entity and claim extraction, and hashed embeddings.

### Groq-Powered Mode

Configure your Groq API key in `.env` and install the Groq package if it is not already included:

```bash
pip install groq
```

Start the application without the offline flag:

```bash
python server/app.py
```

Atlas can then use Groq for LLM-powered answering and extraction.

The configured model must be available to your Groq account. Model availability and rate limits depend on the provider.

For MiniLM embeddings, install `sentence-transformers` if required by your configuration.

---

## Demo

The `samples/` directory contains two fictional PDF documents designed to demonstrate document retrieval and contradiction detection.

Suggested demonstration workflow:

1. Start the Atlas application.
2. Register the first account (which becomes the administrator).
3. Upload sample or custom documents.
4. Wait for document indexing to finish.
5. Ask questions about the uploaded documents.
6. Inspect the supporting evidence and page citations.
7. Explore entities and relationships in the knowledge graph.
8. Review themes, claims, and contradictions.
9. Inspect retrieval performance and evaluation results.

---

## Evaluation and Testing

Run the automated test suite:

```bash
pytest
```

Atlas includes evaluation functionality for comparing retrieval strategies and monitoring answer quality.

The observability module also provides performance traces, latency percentiles, token usage, estimated costs, and grounding-related measurements.

---

## Data Storage

Atlas uses local application storage:

| Data | Location |
|---|---|
| Application database | `.cache/atlas.db` |
| Uploaded documents | `uploads/` |
| Sample documents | `samples/` |
| Knowledge-base files | `data/` |

To reset local application data, stop the application and remove the database and uploaded documents.

**Deployment note:** Local filesystem storage is not automatically persistent on every hosting platform. On free hosting services with ephemeral storage, uploaded documents and the application database may be lost after restarts or redeployments. Use persistent storage or an external database and document store if permanent retention is required.

---

## Deployment

Atlas can be deployed as a Flask web application.

For a small demonstration deployment, the planned hosting option is Render.

General deployment requirements:

1. Push the project to GitHub.
2. Create a Render Web Service connected to the repository.
3. Configure the Python environment and startup command.
4. Add required environment variables through the hosting dashboard.
5. Deploy and verify application functionality.

Do not upload `.env` or credentials to the repository.

For a free-tier demonstration, verify storage limitations before uploading important documents. Free instances may restart or use ephemeral storage.

---

## Current Limitations

- Offline answers are extractive rather than fully generative.
- Offline mode may produce less fluent answers than LLM-powered mode.
- LLM functionality depends on external API availability and rate limits.
- The Neo4j mirror is implemented but requires validation against a live Neo4j server.
- MiniLM and cross-encoder execution paths require environment-specific testing.
- Permanent document storage requires an appropriate persistent storage solution when deployed.

---

## Development Roadmap

Potential future improvements:

- Persistent cloud document storage.
- Production deployment and reliability improvements.
- Advanced graph visualization and exploration.
- Improved retrieval evaluation and benchmarking.
- Better multi-hop reasoning and evidence ranking.
- Expanded authentication and access-control workflows.
- Scalable background document processing.

---

## Project Goal

Atlas explores how knowledge graphs and retrieval-augmented generation can work together to improve multi-document question answering.

The goal is to move beyond simple semantic search and build a system that can connect information, follow relationships, retrieve supporting evidence, and explain how an answer was derived.

---

## Author

**Karthik**

Computer Science and Engineering — AI & Machine Learning

---

## License

Choose and add an appropriate open-source license before distributing this project. Until then, no license is specified.
