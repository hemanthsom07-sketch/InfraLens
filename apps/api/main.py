from fastapi import FastAPI

app = FastAPI(
    title="InfraLens API",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "message": "Welcome to InfraLens 🚀"
    }