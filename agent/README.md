# agent

Python 기반 LLM Agent. 추론·도구 호출·메모리를 담당한다.

## 구조 (`src/`)
| 폴더 | 책임 |
|------|------|
| `core/` | Agent 루프, 실행 오케스트레이션 |
| `graph/` | 추론/워크플로 그래프 정의 |
| `tools/` | Agent가 사용하는 도구 (검색, DB 질의 등) |
| `memory/` | 단기·장기 메모리 (VectorDB 연동) |
| `prompts/` | 프롬프트 템플릿 |

## 원칙
- DB 접근은 `tools/`를 통해 backend의 추상 계층으로만 수행한다.

## 구현 (LangChain / LangGraph ReAct)

LangGraph `create_react_agent` 기반 ReAct 에이전트. OpenAI 호환 LLM 서버(vLLM/TGI 등)에 접속한다.

| 파일 | 역할 |
|------|------|
| `src/core/config.py` | 환경변수 설정 (Zero-Trust: 비밀은 env에서만) |
| `src/core/llm.py` | OpenAI 호환 `ChatOpenAI` 클라이언트 |
| `src/core/agent.py` | `ReActAgent` 오케스트레이션 (run/stream) |
| `src/graph/react_graph.py` | `create_react_agent` 그래프 빌드 |
| `src/tools/` | 도구 레지스트리 (`get_tools()`) + 샘플 도구 |
| `src/memory/short_term.py` | thread별 대화 메모리 (checkpointer) |
| `src/prompts/system.py` | ReAct 시스템 프롬프트 |
| `src/main.py` | 대화형 CLI (루프 검증용) |

### 실행

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env        # LLM_MODEL, LLM_BASE_URL(8000 또는 8080) 설정
python -m src.main
```

### 라이브러리로 사용

```python
from src.core.agent import ReActAgent

agent = ReActAgent()
print(agent.run("2의 10제곱은? 그리고 지금 몇 시야?"))
```

### 도구 추가

`src/tools/`에 `@tool` 함수를 만들고 `src/tools/__init__.py`의 `get_tools()`에 등록한다.
(LAW_SEARCH_TOOL 등 실제 도구는 backend repositories 인터페이스를 통해 연결.)
