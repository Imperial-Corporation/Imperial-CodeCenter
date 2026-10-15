// Exemple TypeScript : client HTTP typé avec délai maximal et erreurs explicites.

export class HttpError extends Error {
    constructor(public status: number, message: string) {
        super(message);
    }
}

export async function getJson<T>(url: string, timeoutMs = 8000): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) {
            throw new HttpError(response.status, `Requête refusée (${response.status})`);
        }
        return (await response.json()) as T;
    } finally {
        clearTimeout(timer);
    }
}

interface Repo {
    name: string;
    stargazers_count: number;
}

getJson<Repo>("https://api.github.com/repos/rust-lang/rust")
    .then((repo) => console.log(repo.name, repo.stargazers_count))
    .catch((error) => console.error(error.message));
