"""Exemple FastAPI : API CRUD en mémoire pour une liste de tâches.

Lancer : uvicorn fastapi_crud:app --reload
Documentation interactive : http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Tâches")


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class Task(TaskIn):
    id: int


tasks: dict[int, Task] = {}
next_id = 1


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(payload: TaskIn) -> Task:
    global next_id
    task = Task(id=next_id, **payload.model_dump())
    tasks[task.id] = task
    next_id += 1
    return task


@app.get("/tasks", response_model=list[Task])
def list_tasks(done: bool | None = None) -> list[Task]:
    items = list(tasks.values())
    return items if done is None else [t for t in items if t.done == done]


@app.put("/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, payload: TaskIn) -> Task:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Tâche introuvable")
    tasks[task_id] = Task(id=task_id, **payload.model_dump())
    return tasks[task_id]


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    if tasks.pop(task_id, None) is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable")
