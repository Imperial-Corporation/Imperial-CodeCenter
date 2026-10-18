// Expressions régulières : extraction, validation et remplacement.
const text = "Contact : ada@example.org, linus@example.com — le 2024-05-17.";

// Adresses e-mail (motif volontairement simple).
const emails = text.match(/[\w.+-]+@[\w-]+\.[\w.]+/g) ?? [];
console.log(emails); // ['ada@example.org', 'linus@example.com']

// Groupes nommés pour décomposer une date ISO.
const date = /(?<annee>\d{4})-(?<mois>\d{2})-(?<jour>\d{2})/.exec(text);
if (date) {
  const { annee, mois, jour } = date.groups;
  console.log(`${jour}/${mois}/${annee}`); // 17/05/2024
}

// Remplacement avec fonction : masque la partie locale des e-mails.
console.log(text.replace(/([\w.+-]+)@/g, (_, local) => local[0] + "***@"));
