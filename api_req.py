from dotenv import load_dotenv
import os
import requests
import json


def main() -> None:
    load_dotenv()
    keys = os.getenv("OPENROUTER_API_KEY_1")
    response = requests.get(
      url="https://openrouter.ai/api/v1/key",
      headers={
        "Authorization": f"Bearer {keys}"
      }
    )

    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
