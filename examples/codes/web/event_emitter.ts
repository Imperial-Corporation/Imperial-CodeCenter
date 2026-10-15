// Émetteur d'événements typé en TypeScript (équivalent minimal de EventEmitter).

type Handler<T> = (payload: T) => void;

export class TypedEmitter<Events extends Record<string, unknown>> {
    private handlers: { [K in keyof Events]?: Handler<Events[K]>[] } = {};

    on<K extends keyof Events>(event: K, handler: Handler<Events[K]>): () => void {
        (this.handlers[event] ??= []).push(handler);
        return () => this.off(event, handler);
    }

    off<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void {
        this.handlers[event] = this.handlers[event]?.filter((h) => h !== handler);
    }

    emit<K extends keyof Events>(event: K, payload: Events[K]): void {
        this.handlers[event]?.forEach((handler) => handler(payload));
    }
}

const emitter = new TypedEmitter<{ login: { user: string }; logout: undefined }>();
const stop = emitter.on("login", ({ user }) => console.log(`Bienvenue ${user}`));
emitter.emit("login", { user: "Ada" });
stop();
