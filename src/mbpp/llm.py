# pyright: reportUnknownMemberType = false
# pyright: reportUnknownVariableType = false
# pyright: reportUnknownArgumentType = false
from openai import OpenAI, RateLimitError
from openai.types.responses import Response
from typing import Any
from time import monotonic, sleep
from openai import BadRequestError


class LLM():
    def __init__(
            self, p_url: str, key_list: list[str]
    ) -> None:
        self.key_list = key_list
        self.current_key = 0
        self.max_key = len(key_list) - 1
        self.provider = p_url
        self.client = self._create_client()
        self.key_available_at = [
            0.0 for _ in self.key_list
        ]

    def _create_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.key_list[self.current_key],
            base_url=self.provider,
            max_retries=0,
        )

    def generate(
            self, model: str, prompt: Any, out: int) -> tuple[Response, int]:
        # try to generate response, if the rate limit threshold is reached,
        # switch key, if no more key available, raises error that stops
        # the function
        tool_error_retries = 0
        while True:
            retries = 0
            print(
                "KEY INDEX:",
                self.current_key,
                "MAX RETRIES:",
                getattr(self.client, "max_retries", "unknown"),
            )
            try:
                if "gpt-oss-120b" in model:
                    raw_response = (self.client.responses.with_raw_response
                                    .create)(
                        input=prompt,
                        reasoning={"effort": "low"},
                        tool_choice="none",
                        temperature=0.5,
                        model=model,
                        max_output_tokens=out,
                        extra_headers={
                            "Groq-Beta": "inference-metrics"
                        }
                    )
                else:
                    raw_response = (self.client.responses.with_raw_response
                                    .create)(
                        input=prompt,
                        model=model,
                        reasoning=None,
                        max_output_tokens=out,
                        extra_headers={
                            "Groq-Beta": "inference-metrics"
                        }
                    )
                headers = raw_response.headers

                print("\n===== RATE LIMIT HEADERS =====")
                print(
                    "limit tokens:",
                    headers.get("x-ratelimit-limit-tokens")
                )
                print(
                    "remaining tokens:",
                    headers.get("x-ratelimit-remaining-tokens")
                )
                print(
                    "reset tokens:",
                    headers.get("x-ratelimit-reset-tokens")
                )
                print(
                    "remaining requests:",
                    headers.get("x-ratelimit-remaining-requests")
                )
                print("==============================\n")

                # Reconstitue le Response habituel
                response = raw_response.parse()

                # ---------- Groq inference metrics ----------
                print("===== INFERENCE METRICS =====")
                print("metadata:", response.metadata)

                if response.metadata:
                    print(
                        "prompt_time:",
                        response.metadata.get("prompt_time")
                    )
                    print(
                        "completion_time:",
                        response.metadata.get("completion_time")
                    )
                    print(
                        "queue_time:",
                        response.metadata.get("queue_time")
                    )
                    print(
                        "total_time:",
                        response.metadata.get("total_time")
                    )

                print("=============================\n")

                return response, retries

            except BadRequestError as e:
                body = e.body if isinstance(e.body, dict) else {}

                if (
                    body.get("code") == "tool_use_failed"
                    and tool_error_retries < 1
                ):
                    tool_error_retries += 1
                    retries += 1
                    continue

            except RateLimitError as e:
                retries += 1

                retry_after = 1.0

                if e.response:
                    value = e.response.headers.get("retry-after")

                    if value is not None:
                        try:
                            retry_after = float(value)
                        except ValueError:
                            retry_after = 1.0

                self.key_available_at[self.current_key] = (
                    monotonic() + retry_after
                )

                print("\n===== RATE LIMIT ERROR =====")
                print("key:", self.current_key)
                print("retry-after:", retry_after)
                print(
                    "available in:",
                    f"{retry_after:.2f}s"
                )

                if e.response:
                    print(
                        "remaining tokens:",
                        e.response.headers.get(
                            "x-ratelimit-remaining-tokens"
                        )
                    )
                    print(
                        "reset tokens:",
                        e.response.headers.get(
                            "x-ratelimit-reset-tokens"
                        )
                    )

                print("============================\n")

                self.switch_key()

    def switch_key(self) -> None:
        now = monotonic()

        # Cherche une autre clé déjà disponible.
        for offset in range(1, len(self.key_list) + 1):
            index = (self.current_key + offset) % len(self.key_list)

            if self.key_available_at[index] <= now:
                self.current_key = index
                self.client = self._create_client()

                print(f"\nSWITCHING API KEY -> {self.current_key}\n")
                return

        # Toutes les clés sont temporairement rate-limited.
        next_index = min(
            range(len(self.key_list)),
            key=lambda i: self.key_available_at[i]
        )

        wait_time = max(
            0.0,
            self.key_available_at[next_index] - monotonic()
        )

        print(
            "\nALL API KEYS TEMPORARILY RATE LIMITED\n"
            f"Waiting {wait_time:.2f}s for key {next_index}\n"
        )

        sleep(wait_time)

        self.current_key = next_index
        self.client = self._create_client()

        print(f"\nREUSING API KEY -> {self.current_key}\n")
