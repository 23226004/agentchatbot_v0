"""repositories — transcript 영속(ConversationRepository). (Design §9)

api→services→repositories. agent 는 이 계층에 비의존(checkpointer 만 DI).
"""

from backend_app.repositories.conversation import (
    ActiveRunExists,
    ConversationRepository,
)

__all__ = ["ConversationRepository", "ActiveRunExists"]
