from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.memory import BaseMemory
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
)


class RedisConversationMemory(BaseMemory):
    """
    Redis-backed LangChain memory adapter.

    It keeps the existing `chat_history` contract without using the
    deprecated ConversationBufferMemory class.
    """

    store: Any
    session_id: str
    memory_key: str = "chat_history"
    input_key: str = "input"
    output_key: str = "output"
    return_messages: bool = True

    @property
    def memory_variables(
        self,
    ) -> list[str]:
        return [
            self.memory_key
        ]

    def _messages(self):
        values = (
            self.store.list_messages(
                self.session_id
            )
        )

        messages = []

        for item in values:
            role = item.get(
                "role"
            )

            content = str(
                item.get(
                    "content"
                )
                or ""
            )

            if role == "user":
                messages.append(
                    HumanMessage(
                        content=content
                    )
                )

            elif role == "assistant":
                messages.append(
                    AIMessage(
                        content=content
                    )
                )

        return messages

    def load_memory_variables(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        messages = (
            self._messages()
        )

        if self.return_messages:
            value = messages
        else:
            value = "\n".join(
                (
                    f"{type(message).__name__}: "
                    f"{message.content}"
                )
                for message in messages
            )

        return {
            self.memory_key:
                value
        }

    async def aload_memory_variables(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.load_memory_variables,
            inputs,
        )

    def save_context(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        user_content = (
            inputs.get(
                self.input_key
            )
        )

        assistant_content = (
            outputs.get(
                self.output_key
            )
        )

        if user_content is not None:
            self.store.append(
                self.session_id,
                "user",
                str(user_content),
            )

        if assistant_content is not None:
            self.store.append(
                self.session_id,
                "assistant",
                str(assistant_content),
            )

    async def asave_context(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self.save_context,
            inputs,
            outputs,
        )

    def clear(self) -> None:
        self.store.clear(
            self.session_id
        )

    async def aclear(self) -> None:
        await asyncio.to_thread(
            self.clear
        )