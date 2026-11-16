from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    demo_mode: bool = False
    storage_path: str = ".storage"
    sms_mode: Literal["disabled", "demo"] = "disabled"
    allowed_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="after")
    def enforce_sms_boundary(self):
        if self.sms_mode == "demo" and not self.demo_mode:
            raise ValueError("Demo SMS must never be enabled outside DEMO_MODE")
        return self


settings = Settings()
