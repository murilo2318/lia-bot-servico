# Lia — backend (o cérebro)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # preencha GEMINI_API_KEY (ou use LLM_PROVIDER=mock)
uvicorn app.main:app --port 8000
```

Documentação interativa: http://localhost:8000/docs (clique em *Authorize* e informe o `API_KEY`).

Testes (rodam com o provedor mock, sem chave e sem internet): `python -m pytest -q`

## Avaliação: teste A/B e LLM-as-judge

Com o backend rodando, em outro terminal (dentro de `backend/`):

```bash
# v1 (prompt do Build Day) × v2 (cinco camadas), 18 conversas cada, com juiz
python scripts/ab_test.py --julgar

# comparar modelos em vez de prompts
python scripts/ab_test.py --variantes v2 --provedores groq,gemini --julgar

# rodar só algumas conversas (útil para testar rápido)
python scripts/ab_test.py --so T1,T5,E2

# reavaliar o último A/B salvo com o juiz, sem conversar com o bot de novo
python scripts/llm_judge.py --rejulgar --juiz groq:qwen/qwen3.8-27b

# medir com o modelo real quanto a Lia entende erros de digitação e paráfrases
python scripts/robustez.py

# avaliar com o juiz uma conversa feita pela tela (session_id aparece na barra lateral)
python scripts/llm_judge.py --sessao <session_id>
```

- As conversas estão em `scripts/conversas.py`: T1–T8 do enunciado e E1–E10 de alunos. A E2 é "informativa":
  depende de o LLM entender uma paráfrase, então mostra a diferença entre variantes sem contar no acerto do roteiro.
- **Rubrica v2 do juiz:** cinco critérios (relevância, aderência ao papel, retenção de contexto, clareza e
  fidelidade à base). O juiz recebe a FAQ e a agenda como referência, segue uma escala ancorada e justifica
  antes de dar as notas. A v1, sem referência, deu 5,0 em quase tudo e não detectou uma resposta que
  extrapolava a FAQ. O relatório lista as conversas com nota ≤ 3 e o trecho problemático.
- O juiz usa **o provedor oposto ao do bot** (`--juiz auto`, o padrão: com o bot no Groq, o juiz é o Gemini),
  para o modelo não julgar a si mesmo. Dá para escolher outro: `--juiz groq:qwen/qwen3.8-27b`.
- No A/B publicado em `docs/metricas.md` o juiz foi o `qwen/qwen3.8-27b` (família Qwen, da Alibaba) no Groq:
  mesmo provedor do bot (`openai/gpt-oss-120b`), mas família de modelo diferente. O `--juiz auto` escolheria o
  Gemini, mas a cota gratuita dele não comportava as 36 avaliações.
- Antes de rodar, `python scripts/diagnostico_llm.py groq` e `... gemini` mostram se cada provedor está no ar.
- Os relatórios vão para `scripts/resultados/ab_<data>.md` (tabela) e `.json` (transcrições completas).
- Os scripts **não enviam CSAT**: nota de satisfação só vem de pessoas reais, pela tela.
- Plano gratuito: há uma pausa entre mensagens (`--pausa 2`) e novas tentativas automáticas em caso de 503.
  Se o provedor limitar muito, aumente para `--pausa 6`.
