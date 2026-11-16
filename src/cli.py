"""Command-line interface.

    python src/cli.py doctor            check configuration and the database
    python src/cli.py setup             create constraints + vector index
    python src/cli.py build [--reset]   extract the knowledge graph, then embed
    python src/cli.py embed             embed new/changed chunks only
    python src/cli.py ask "question"    answer a question (omit for chat mode)
    python src/cli.py stats             graph and embedding statistics
    python src/cli.py reset --yes       delete all project graph data
"""

import argparse
import logging
import os
import sys

from config import EMBEDDING_DIMENSIONS, VECTOR_INDEX_NAME
from retrievers import MODES


def _print_stats(stats: dict) -> None:
    print("\nNodes:")
    for row in stats["nodes"]:
        print(f"  {row['label']:<10} {row['count']}")

    print("\nRelationships:")
    for row in stats["relationships"]:
        print(f"  {row['type']:<12} {row['count']}")

    print("\nMost connected entities:")
    for row in stats["top_entities"]:
        print(f"  {row['name']} ({row['type']}) - degree {row['degree']}")

    print("\nEmbeddings:", stats["embedding"])
    print("Quality:", stats["quality"])

    if stats["legacy"].get("entities_without_key"):
        print("\nWARNING: entities without a canonical key exist (old data). "
              "Run: python src/cli.py build --reset")


def cmd_doctor(args) -> int:
    ok = True

    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "GROQ_API_KEY"):
        present = bool(os.getenv(name))
        ok &= present
        print(f"[{'ok' if present else 'MISSING'}] {name}")

    if not ok:
        print("\nFill in .env (see .env.example) and run again.")
        return 1

    from graph_schema import vector_index_status
    from graph_storage import GraphStorage

    storage = GraphStorage(ensure=False)

    try:
        print("[ok] Neo4j connection")
        index = vector_index_status(storage.db)

        if index is None:
            print(f"[MISSING] vector index '{VECTOR_INDEX_NAME}' "
                  "-> run: python src/cli.py setup")
        else:
            print(f"[ok] vector index '{VECTOR_INDEX_NAME}' is {index['state']} "
                  f"(expects {EMBEDDING_DIMENSIONS} dimensions)")

        print("Embeddings:", storage.get_embedding_status())
    finally:
        storage.close()

    return 0


def cmd_setup(args) -> int:
    from graph_schema import ensure_schema
    from neo4j_connection import Neo4jConnection

    with Neo4jConnection() as db:
        db.verify_connection()
        ensure_schema(db)

    print("Schema ready.")
    return 0


def cmd_build(args) -> int:
    from build_graph import build_knowledge_graph
    from embeddings import embed_missing_chunks

    summary = build_knowledge_graph(
        reset=args.reset, force=args.force, prune=not args.no_prune,
        limit=args.limit, sleep=args.sleep,
    )
    print("\nGraph build summary:", summary)

    if summary["failed"]:
        print("Some chunks failed extraction; re-run `build` to retry them:",
              summary["failed"])

    if not args.skip_embed:
        print("\nEmbedding:", embed_missing_chunks())

    return 1 if summary["failed"] else 0


def cmd_embed(args) -> int:
    from embeddings import embed_missing_chunks

    print(embed_missing_chunks())
    return 0


def _show_result(result: dict, show_context: bool) -> None:
    print("\n" + "=" * 70)
    print(f"ANSWER ({result['mode']})")
    print("=" * 70)
    print(result["answer"])

    print("\nSOURCES")
    for number, chunk in enumerate(result["chunks"], start=1):
        methods = "+".join(chunk.get("retrieval_sources") or [result["mode"]])
        print(f"  [{number}] {chunk['chunk_id']} | {chunk.get('source')} | {methods}")

    trace = result.get("trace") or {}
    if trace.get("seeds"):
        print("\nGraph entities linked from the question:", ", ".join(trace["seeds"]))

    facts = {
        f"{r['entity1']} --[{r['relationship']}]--> {r['entity2']}"
        for chunk in result["chunks"]
        for r in chunk.get("matched_relationships") or []
    }
    if show_context and facts:
        print("\nGraph facts used:")
        for fact in sorted(facts):
            print("  ", fact)

    print(f"\nRetrieval {result['retrieval_seconds']}s | "
          f"generation {result['generation_seconds']}s")


def cmd_ask(args) -> int:
    from rag_pipeline import RAGPipeline

    with RAGPipeline(mode=args.mode, top_k=args.top_k,
                     max_hops=args.hops) as pipeline:
        if args.question:
            _show_result(pipeline.ask(args.question), args.show_context)
            return 0

        print(f"GraphRAG chat ({args.mode}). Type 'exit' to quit.")

        while True:
            try:
                question = input("\nQuestion: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if question.lower() in {"exit", "quit"}:
                break

            if not question:
                continue

            try:
                _show_result(pipeline.ask(question), args.show_context)
            except Exception as error:  # keep the chat alive
                print(f"Error: {type(error).__name__}: {error}")

    return 0


def cmd_stats(args) -> int:
    from graph_storage import GraphStorage

    storage = GraphStorage(ensure=False)

    try:
        _print_stats(storage.get_statistics())
    finally:
        storage.close()

    return 0


def cmd_reset(args) -> int:
    if not args.yes:
        print("This deletes all Chunk/Entity/Document nodes. Re-run with --yes.")
        return 1

    from graph_storage import GraphStorage

    storage = GraphStorage(ensure=False)

    try:
        storage.reset_graph()
    finally:
        storage.close()

    print("Graph data deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphrag", description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("setup").set_defaults(func=cmd_setup)
    sub.add_parser("embed").set_defaults(func=cmd_embed)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    build = sub.add_parser("build")
    build.add_argument("--reset", action="store_true",
                       help="wipe existing graph data first")
    build.add_argument("--force", action="store_true",
                       help="re-extract unchanged chunks too")
    build.add_argument("--no-prune", action="store_true")
    build.add_argument("--skip-embed", action="store_true")
    build.add_argument("--limit", type=int, help="process at most N chunks")
    build.add_argument("--sleep", type=float, default=0.0,
                       help="seconds to wait between LLM calls (rate limits)")
    build.set_defaults(func=cmd_build)

    ask = sub.add_parser("ask")
    ask.add_argument("question", nargs="?")
    ask.add_argument("--mode", choices=MODES, default="hybrid")
    ask.add_argument("--top-k", type=int, default=5)
    ask.add_argument("--hops", type=int, default=2)
    ask.add_argument("--show-context", action="store_true")
    ask.set_defaults(func=cmd_ask)

    reset = sub.add_parser("reset")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    try:
        return args.func(args)
    except Exception as error:
        logging.getLogger(__name__).error("%s: %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
