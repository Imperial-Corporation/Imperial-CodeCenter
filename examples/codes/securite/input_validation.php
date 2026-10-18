<?php
// Validation d'entrées utilisateur : liste blanche et filtres natifs.
function validateInput(array $input): array
{
    $errors = [];

    $email = filter_var($input['email'] ?? '', FILTER_VALIDATE_EMAIL);
    if ($email === false) {
        $errors['email'] = 'Adresse e-mail invalide';
    }

    $age = filter_var($input['age'] ?? null, FILTER_VALIDATE_INT, [
        'options' => ['min_range' => 0, 'max_range' => 150],
    ]);
    if ($age === false || $age === null) {
        $errors['age'] = 'Âge invalide (0 à 150)';
    }

    if (!preg_match('/^[a-z0-9_]{3,20}$/', $input['pseudo'] ?? '')) {
        $errors['pseudo'] = '3 à 20 caractères : minuscules, chiffres, _';
    }

    return $errors;
}

print_r(validateInput(['email' => 'ada@example.org', 'age' => '36', 'pseudo' => 'ada_l']));
print_r(validateInput(['email' => 'pas-un-mail', 'age' => '-4', 'pseudo' => 'X']));
