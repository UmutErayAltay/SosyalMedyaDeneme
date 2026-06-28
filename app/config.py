"""Yapılandırma - .env'den yüklenir."""
import os
from dotenv import load_dotenv

# .env dosyasını proje kökünden yükle
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


class Config:
    # Flask
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "degistir-beni")

    # Supabase
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")  # service role - sunucu tarafı only
    SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL")
