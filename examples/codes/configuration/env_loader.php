<?php
// Charge un fichier .env (CLE=valeur) avec valeurs par défaut.
function loadEnv(string $path): array
{
    $values = [];
    if (!is_readable($path)) {
        return $values;
    }
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || !str_contains($line, '=')) {
            continue; // commentaire ou ligne invalide
        }
        [$key, $value] = explode('=', $line, 2);
        $values[trim($key)] = trim($value, " \t\"'");
    }
    return $values;
}

$file = tempnam(sys_get_temp_dir(), 'env');
file_put_contents($file, "# exemple\nPORT=8080\nNAME=\"demo\"\n");
$env = loadEnv($file) + ['DEBUG' => 'false'];
unlink($file);

echo "Port : {$env['PORT']}, debug : {$env['DEBUG']}\n";
