<?php
// Exemple PHP : mini routeur avec expressions régulières et réponses JSON.

declare(strict_types=1);

function json_response(array $data, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
}

$routes = [
    'GET' => [
        '#^/hello/(\w+)$#' => fn(string $name) => json_response(['message' => "Bonjour $name"]),
        '#^/time$#'        => fn() => json_response(['now' => date(DATE_ATOM)]),
    ],
];

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);

foreach ($routes[$method] ?? [] as $pattern => $handler) {
    if (preg_match($pattern, $path, $matches)) {
        $handler(...array_slice($matches, 1));
        return;
    }
}

json_response(['error' => 'Route inconnue'], 404);
