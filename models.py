from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ProxyItem(BaseModel):
    id: Optional[int] = None
    ip: str
    port: int
    protocol: str = Field(default="http", description="http, https, socks4, or socks5")
    username: Optional[str] = None
    password: Optional[str] = None
    country: str = Field(default="UNKNOWN", description="2-letter ISO country code (e.g. PH, US)")
    country_name: Optional[str] = None
    latency: Optional[int] = Field(default=None, description="Round-trip latency in milliseconds")
    anonymity: Optional[str] = Field(default="unknown", description="transparent, anonymous, elite")
    score: int = Field(default=100, description="Reliability score, 0-100")
    fail_count: int = Field(default=0, description="Consecutive failure count")
    is_active: bool = Field(default=True, description="Whether the proxy passed health check")
    source: str = Field(default="manual", description="Source where proxy was scraped")

    # IP Purity & Risk Attributes
    ip_type: str = Field(default="unknown", description="residential, datacenter, mobile, or unknown")
    fraud_score: int = Field(default=0, description="Fraud score 0-100, lower is cleaner")
    google_clean: bool = Field(default=False, description="True if Google search passes without captcha")
    clean_level: str = Field(default="C", description="A (Ultra clean/Residential), B (Good), C (Datacenter), D (Risk)")

    last_checked: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_url(self) -> str:
        """Returns standard proxy URL string: protocol://[user:pass@]ip:port"""
        auth = f"{self.username}:{self.password}@" if self.username and self.password else ""
        return f"{self.protocol.lower()}://{auth}{self.ip}:{self.port}"

    def to_compact_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ip": self.ip,
            "port": self.port,
            "protocol": self.protocol,
            "username": self.username,
            "password": self.password,
            "country": self.country,
            "country_name": self.country_name,
            "latency": self.latency,
            "anonymity": self.anonymity,
            "ip_type": self.ip_type,
            "fraud_score": self.fraud_score,
            "google_clean": self.google_clean,
            "clean_level": self.clean_level,
            "score": self.score,
            "is_active": self.is_active,
            "proxy_url": self.to_url(),
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


class ProxyQuery(BaseModel):
    country: Optional[str] = None
    protocol: Optional[str] = None
    max_latency: Optional[int] = None
    min_score: Optional[int] = 50
    is_active: Optional[bool] = True
    clean_only: Optional[bool] = None
    clean_level: Optional[str] = None
    ip_type: Optional[str] = None
    limit: int = 20
    offset: int = 0


class StatsResponse(BaseModel):
    total_proxies: int
    active_proxies: int
    clean_proxies: int = 0
    residential_proxies: int = 0
    by_country: Dict[str, int]
    by_protocol: Dict[str, int]
    by_clean_level: Dict[str, int] = {}
    avg_latency_ms: Optional[float] = None
