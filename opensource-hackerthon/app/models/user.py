from app.core.database import Base
from sqlalchemy import Column, Integer, String, Text

class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    spotify_id = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True) 
    token_expires_at = Column(Integer, nullable=True)