// Échappement HTML pour éviter les injections XSS lors de l'affichage.
const ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ENTITIES[char]);
}

const comment = '<script>alert("xss")</script> & "merci"';
console.log(escapeHtml(comment));
// &lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt; &amp; &quot;merci&quot;
