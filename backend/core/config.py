from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Application settings
    APP_NAME: str = "Smart Canteen"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database settings
    DATABASE_URL: str = Field(default="sqlite:///./smart_canteen.db", env="DATABASE_URL")

    # Security settings
    SECRET_KEY: str = Field(default="CHANGE_ME_TO_A_SECURE_RANDOM_KEY", env="SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # QR code settings
    QR_CODE_BASE_URL: str = Field(default="http://localhost:8000", env="QR_CODE_BASE_URL")

    # Admin settings
    FIRST_ADMIN_EMAIL: str = Field(default="admin@example.com", env="FIRST_ADMIN_EMAIL")
    FIRST_ADMIN_PASSWORD: str = Field(default="CHANGE_ME", env="FIRST_ADMIN_PASSWORD")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8')

settings = Settings()