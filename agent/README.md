# agent

Python 기반 LLM Agent. 추론·도구 호출·메모리를 담당한다.

## 구조 (`src/agent_app/`)
| 폴더 | 책임 |
|------|------|
| `core/` | Agent 루프, 실행 오케스트레이션 |
| `graph/` | 추론/워크플로 그래프 정의 |
| `tools/` | Agent가 사용하는 도구 (검색, DB 질의 등) |
| `memory/` | 단기 대화 메모리 (thread별 checkpointer, 기본 인메모리) |
| `prompts/` | 프롬프트 템플릿 |

## 원칙
- 법령 DB 접근은 `tools/`(search_legal)를 통해 **legal_core RetrievalService(GraphRAG 추상)**로만 수행한다.

## 구현 (LangChain / LangGraph ReAct)

LangGraph `create_react_agent` 기반 ReAct 에이전트. **OpenAI(GPT) 또는 OpenAI 호환 서버**(vLLM/TGI/llama.cpp 등)에 접속한다 — 우선순위: `OPENAI_API`+`GPT_MODEL`(OpenAI 정식) → `LLM_*`(호환 서버). config.py 참고.

| 파일 | 역할 |
|------|------|
| `src/agent_app/core/config.py` | 환경변수 설정 (Zero-Trust: 비밀은 env에서만) |
| `src/agent_app/core/llm.py` | OpenAI 호환 `ChatOpenAI` 클라이언트 |
| `src/agent_app/core/agent.py` | `ReActAgent` 오케스트레이션 (run/stream) |
| `src/agent_app/graph/react_graph.py` | `create_react_agent` 그래프 빌드 |
| `src/agent_app/tools/` | 도구 레지스트리 (`get_tools()`) + 샘플 도구 |
| `src/agent_app/memory/short_term.py` | thread별 대화 메모리 (checkpointer) |
| `src/agent_app/prompts/system.py` | ReAct 시스템 프롬프트 |
| `src/agent_app/main.py` | 대화형 CLI (루프 검증용) |

### 실행

```bash
cd agent
uv venv                              # .venv 없으면 생성(fresh clone 필수 — uv 관리, pip 없음)
uv pip install -r requirements.txt   # agent_app(`-e .`) + legal_core/legal_infra(editable) + PyPI 의존
cp .env.example .env        # GPT: OPENAI_API+GPT_MODEL / 호환서버: LLM_MODEL, LLM_BASE_URL(8000·8080)
python -m agent_app.main
```

### 라이브러리로 사용

```python
from agent_app.core.agent import ReActAgent

agent = ReActAgent()
print(agent.run("2의 10제곱은? 그리고 지금 몇 시야?"))
```

### 도구 추가

`src/agent_app/tools/`에 `@tool` 함수를 만들고 `src/agent_app/tools/__init__.py`의 `get_tools()`에 등록한다.
현재 등록 도구: `calculator`·`current_time`·`text_stats`·**`search_legal`**(법령 검색, legal_core RetrievalService 경유).
