// Kotlin : coroutines structurées avec async/await et délai maximal.
import kotlinx.coroutines.*

suspend fun loadValue(name: String, delayMs: Long): String {
    delay(delayMs)
    return "valeur de $name"
}

fun main() = runBlocking {
    val result = withTimeoutOrNull(1_000) {
        val a = async { loadValue("a", 200) }
        val b = async { loadValue("b", 300) }
        "${a.await()} + ${b.await()}"
    }

    println(result ?: "délai dépassé")
}
