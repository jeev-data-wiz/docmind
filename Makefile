# DocMind AI Agent — Makefile
# Usage: make <target>

.PHONY: install ingest query agent eval interactive clean help

help:
	@echo "DocMind AI Agent — Available Commands"
	@echo "======================================"
	@echo "  make install       Install Python dependencies"
	@echo "  make ingest        Ingest corpus directory into vector store"
	@echo "  make interactive   Start interactive agent REPL"
	@echo "  make eval          Run the evaluation suite"
	@echo "  make clean         Delete the vector store (re-ingest required)"
	@echo ""
	@echo "  make query Q=\"your question here\"    Single RAG query"
	@echo "  make agent Q=\"your question here\"    Agentic reasoning loop"

install:
	pip install -r requirements.txt

ingest:
	python main.py ingest --corpus-dir corpus

query:
	@[ "$(Q)" ] || (echo "Usage: make query Q=\"your question\"" && exit 1)
	python main.py query "$(Q)"

agent:
	@[ "$(Q)" ] || (echo "Usage: make agent Q=\"your question\"" && exit 1)
	python main.py agent "$(Q)"

eval:
	python main.py eval

interactive:
	python main.py interactive

clean:
	rm -rf .vectorstore logs/
	@echo "Vector store and logs cleared. Run 'make ingest' to re-index."
