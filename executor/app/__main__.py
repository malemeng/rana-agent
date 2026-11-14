import os

import uvicorn
from app.config import get_settings


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
