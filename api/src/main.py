from fastapi import FastAPI

app = FastAPI(title="mras-ops")


@app.get("/health")
def health():
    return {"status": "ok"}
