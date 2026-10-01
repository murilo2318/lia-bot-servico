# Relatório de métricas — Lia

> ✏️ Este arquivo tem uma página. As seções 2 e 3 recebem os números reais; as seções 4 e 5 são escritas pelo
> grupo. Apaguem estas notas e as perguntas-guia antes de entregar.

## 1. Como os dados foram gerados

- **Modelo:** `openai/gpt-oss-120b` no Groq (bot) e `gemini-3.5-flash-lite` (reserva e juiz), em ✏️ data.
- **Conversas em lote:** as 18 de `backend/scripts/conversas.py` (T1–T8 do enunciado e E1–E10 de alunos),
  rodadas com `python scripts/ab_test.py --julgar` nas variantes v1 (prompt do Build Day) e v2 (cinco camadas).
- **Conversas manuais:** ✏️ N conversas feitas pelos integrantes na tela, com nota de CSAT ao final.
- **Fonte dos números:** `GET /metrics`, calculado sobre o log de cada turno no SQLite. O CSAT vem só das
  conversas manuais: os scripts não enviam nota.

## 2. Retorno do /metrics

✏️ Colem os valores de `GET /metrics` (sem filtro) depois de todas as conversas.

| Métrica | Valor |
|---|---|
| Conversas | |
| Taxa de contenção | |
| Taxa de fallback | |
| Taxa de handoff | |
| Mensagens por conversa | |
| FAQ-hit | |
| CSAT: conversas sem handoff / com handoff | |
| Latência média / p95 | |
| Fallback por motivo | |
| Intenção classificada por regra / LLM / memória | |

## 3. Teste A/B: v1 (Build Day) × v2 (cinco camadas)

✏️ Colem a tabela de `backend/scripts/resultados/ab_<data>_rejulgado.md` (juiz com a rubrica v2).
Atenção: contenção e handoff aqui refletem o desenho do roteiro (4 das 18 conversas provocam handoff de
propósito), e não o uso real; para uso real, olhem a seção 2.

| Variante | Contenção | Fallback | Handoff | Msgs/conv. | Acerto do roteiro | Acerto em paráfrases | Juiz: relevância | Juiz: papel | Juiz: contexto | Juiz: clareza | Juiz: fidelidade |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 | | | | | | | | | | | |
| v2 | | | | | | | | | | | |

## 4. Leitura crítica

✏️ Um insight que os dados mostram, em um parágrafo. Perguntas que podem ajudar a encontrar:
- Onde a Lia mais cai em fallback? Olhem "fallback por motivo" e a lista de mensagens mascaradas no painel.
  São paráfrases de temas que existem na FAQ, ou temas que faltam?
- O que o LLM está resolvendo que as regras não resolvem? Comparem a parcela de intenções classificadas pelo
  LLM com a conversa E2.
- O CSAT das conversas contidas é parecido com o das que foram ao professor? Se for mais baixo, o que isso diz
  sobre a taxa de contenção (o "erro clássico" da Aula 4)?
- A v2 ganhou da v1 em que critério do juiz? Em algum ela perdeu? Se as métricas operacionais quase não mudam
  entre as versões, o que isso diz sobre onde está o comportamento da Lia: no prompt ou no código?
- O que a seção "Conversas com alguma nota do juiz ≤ 3" mostra? Há respostas que extrapolam a FAQ?
- Por que a primeira versão do juiz deu 5,0 em quase tudo? O que isso ensina sobre LLM-as-judge?

## 5. Proposta de melhoria

✏️ Uma mudança concreta, ligada ao insight da seção 4, no formato do loop de otimização da Aula 4:

- **Problema observado:**
- **Mudança proposta:** (no arquivo `______`)
- **Métrica que deve mudar e quanto esperamos:**
- **Como vamos medir:** (qual conversa ou script rodar de novo)
