# Relatório de métricas — Lia

> ✏️ As seções 4 e 5 são escritas pelo grupo. Apaguem esta nota e as perguntas-guia antes de entregar.

## 1. Como os dados foram gerados

- **Modelo:** `openai/gpt-oss-120b` no Groq (bot), `gemini-3.5-flash-lite` como reserva, `qwen/qwen3.8-27b` no
  Groq como juiz (família Qwen, da Alibaba: mesmo provedor do bot, família de modelo diferente). O juiz automático
  do código (`--juiz auto`) escolheria o Gemini, mas a cota gratuita dele não comportava as 36 avaliações, por isso
  usamos o Qwen. Prompt v2 (cinco camadas) nas conversas manuais.
- **Conversas manuais (seção 2):** 10 conversas em 02/10/2026, feitas por um integrante interpretando 10 perfis de
  aluno, com a nota de CSAT dada do ponto de vista de cada perfil (método e mensagens em `perfis_conversas.md`).
  Uma rodada anterior encontrou 15 problemas, todos corrigidos antes desta (`rodada1_achados.md`).
- **Conversas em lote (seção 3):** as 18 de `backend/scripts/conversas.py` (T1–T8 e E1–E10), nas variantes v1
  (prompt do Build Day) e v2, avaliadas por LLM-as-judge com rubrica de 5 critérios (rubrica v2.2, com a FAQ e as
  regras do sistema como referência). Os scripts não enviam CSAT.
- **Fonte:** `GET /metrics`, calculado sobre o log de cada turno; cópia em `docs/metricas_rodada2.json`.

## 2. Uso: conversas manuais (rodada 2)

| Métrica | Valor |
|---|---|
| Conversas / turnos do usuário | 10 / 65 |
| Taxa de contenção | 80% |
| Taxa de fallback | 4,6% (3 turnos: 2 fora do escopo, 1 fora da base) |
| Taxa de handoff | 20% (1 frustração, 1 tema sensível) |
| Mensagens por conversa | 6,5 |
| FAQ-hit | 24,6% |
| CSAT geral / sem handoff / com handoff | 3,6 / 4,0 / 2,0 |
| Intenção classificada por regra / memória / LLM | 83% / 9% / 8% |
| Eventos de guardrail | 2 prompt injection, 2 pedido indevido |
| Latência média / p95 | 297 ms / 1.104 ms |
| Tokens (entrada / saída) | 16.349 / 3.083 |

## 3. Teste A/B: v1 (Build Day) × v2 (cinco camadas), 18 conversas cada

| Variante | Contenção | Fallback | Handoff | Msgs/conv. | Acerto do roteiro | Acerto em paráfrases | Latência média | Juiz: relevância | Juiz: papel | Juiz: contexto | Juiz: clareza | Juiz: fidelidade |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 78% | 9% | 22% | 4,33 | 100% | 50% | 563 ms | 4,94 | 4,94 | 4,78 | 5,00 | 4,39 |
| v2 | 78% | 9% | 22% | 4,33 | 100% | 50% | 746 ms | 4,94 | 5,00 | 4,83 | 4,89 | 4,56 |

Contenção e handoff aqui refletem o desenho do roteiro (4 das 18 conversas provocam handoff de propósito). Na
rodada anterior do mesmo A/B, a fidelidade foi 4,50 (v1) × 4,22 (v2), com a ordem invertida. Depois da ampliação de
linguagem e da base, o roteiro (v2, sem juiz) passou a 100% também nas paráfrases, com fallback de 6%.

## 4. Leitura crítica

✏️ Um insight que os dados mostram, em um parágrafo. Perguntas que podem ajudar:
- O CSAT das conversas com handoff (2,0) é metade do das contidas (4,0). O handoff aconteceu na hora certa? O que
  o aluno que é transferido sente, e o que a Lia poderia dizer ou fazer de diferente?
- 83% das intenções foram resolvidas por regra. O que isso diz sobre custo, latência e previsibilidade? E sobre
  onde está o comportamento da Lia: no prompt ou no código? (Compare com o empate entre v1 e v2.)
- A fidelidade à base é o critério mais fraco do juiz. Nas conversas do Bruno, cada "sim" a uma oferta criada
  pelo modelo levou a conteúdo inventado. Por que as respostas sempre terminam com uma oferta?
- A primeira versão do juiz deu 5,0 em quase tudo; com a FAQ como referência, passou a apontar problemas reais,
  e também alguns falsos positivos. O que isso ensina sobre LLM-as-judge?

## 5. Proposta de melhoria

✏️ Uma mudança concreta, ligada ao insight da seção 4, no formato do loop de otimização da Aula 4:

- **Problema observado:**
- **Mudança proposta:** (no arquivo `______`)
- **Métrica que deve mudar e quanto esperamos:**
- **Como vamos medir:** (qual conversa ou script rodar de novo)
