"""Petite API de démonstration pour la plateforme DevSecOps.

Volontairement simple : l'intérêt du projet est la chaîne CI/CD sécurisée
qui l'entoure, pas l'application elle-même.
"""

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="DevSecOps Demo API", version=os.getenv("APP_VERSION", "dev"))

# Stockage en mémoire (suffisant pour la démo)
_items: dict[int, "Item"] = {}


class Item(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)


@app.get("/health")
def health() -> dict:
    """Sonde utilisée par Kubernetes (liveness/readiness)."""
    return {"status": "ok"}


@app.get("/version")
def version() -> dict:
    return {"version": app.version}


@app.get("/items")
def list_items() -> dict[int, Item]:
    return _items


@app.post("/items/{item_id}", status_code=201)
def create_item(item_id: int, item: Item) -> Item:
    if item_id in _items:
        raise HTTPException(status_code=409, detail="Item already exists")
    _items[item_id] = item
    return item


@app.get("/items/{item_id}")
def get_item(item_id: int) -> Item:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    return _items[item_id]
