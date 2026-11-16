"""Generate the sample PDFs in samples/ (fictional 'Orion DB' documents with deliberate conflicts)."""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

OUT = Path(__file__).resolve().parent.parent / "samples"
OUT.mkdir(exist_ok=True)
S = getSampleStyleSheet()

def build(name, title, pages):
    doc = SimpleDocTemplate(str(OUT / name), pagesize=A4, title=title)
    flow = [Paragraph(title, S["Title"]), Spacer(1, 12)]
    for i, (head, paras) in enumerate(pages):
        if i:
            flow.append(PageBreak())
        flow.append(Paragraph(head, S["Heading2"]))
        for p in paras:
            flow += [Paragraph(p, S["BodyText"]), Spacer(1, 8)]
    doc.build(flow)

build("orion_architecture_guide.pdf", "Orion DB Architecture Guide", [
    ("1. Overview", [
        "Orion DB is a vector-native graph database created by Maria Lund in 2015. It is written in Rust and released under a proprietary licence.",
        "Orion DB stores embeddings next to graph nodes so that similarity search and traversal run in one query. Retrieval-Augmented Generation (RAG) applications use Orion DB as their knowledge store."]),
    ("2. Indexing", [
        "Orion DB uses 384 dimensions for every stored embedding. The index is based on HNSW, a Hierarchical Navigable Small World graph proposed by Yu. A. Malkov.",
        "Orion DB supports ACID transactions across graph writes and vector writes. The default value of M is 16 connections per node."]),
    ("3. Deployment", [
        "Orion DB is distributed and replicates every shard three times. Orion Cloud, the managed service built on Orion DB, is located in Oslo.",
        "Orion Cloud uses Kubernetes for orchestration and supports automatic scaling."]),
])
build("orion_field_report.pdf", "Orion DB Field Report", [
    ("Summary", [
        "This report summarises two years of production experience with Orion DB at three customers. Teams praised the unified query model."]),
    ("Findings", [
        "Orion DB was created by Jonas Weber in 2017 according to the vendor history. Orion DB is written in Go and ships as a single binary.",
        "Orion DB uses 768 dimensions for embeddings in the reference configuration. It does not support ACID transactions across vector writes.",
        "Orion DB is open source and available under the Apache licence."]),
    ("Operations", [
        "Orion Cloud is located in Frankfurt. Orion DB defaults to 32 connections per node in the tuning guide.",
        "Retrieval-Augmented Generation (RAG) systems built on Orion DB showed lower latency than on separate stores."]),
])
print("samples written to", OUT)
