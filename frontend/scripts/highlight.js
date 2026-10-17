// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Coloration syntaxique SÛRE : le code est découpé en jetons puis converti en
 * nœuds DOM (<span class="tok-…">) avec textContent. Aucun innerHTML : le code
 * affiché, même hostile, n'est jamais interprété.
 *
 * `ICC.highlight.render(code, language)` retourne un DocumentFragment.
 * Un langage inconnu ou un code très long reste en texte brut.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const MAX_CHARS = 60000;

    const words = (list) => `\\b(?:${list.trim().split(/\s+/).join("|")})\\b`;

    const NUMBER = "\\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\\d[\\d_]*(?:\\.\\d[\\d_]*)?(?:[eE][+-]?\\d+)?[a-zA-Z]{0,3})\\b";
    const DQ = '"(?:[^"\\\\\\n]|\\\\.)*"';
    const SQ = "'(?:[^'\\\\\\n]|\\\\.)*'";
    const BT = "`(?:[^`\\\\]|\\\\[\\s\\S])*`";
    const FUNC = "\\b[A-Za-z_][A-Za-z0-9_]*(?=\\s*\\()";
    const TYPE = "\\b[A-Z][A-Za-z0-9_]*\\b";

    /* Chaque règle : [classe, source de l'expression]. Ordre = priorité. */
    const LANGS = {};

    LANGS.python = [
        ["comment", "#[^\\n]*"],
        ["string", '(?:[rRbBfFuU]{0,2})(?:"""[\\s\\S]*?(?:"""|$)|\'\'\'[\\s\\S]*?(?:\'\'\'|$))'],
        ["string", `(?:[rRbBfFuU]{0,2})(?:${DQ}|${SQ})`],
        ["meta", "@[A-Za-z_][\\w.]*"],
        ["keyword", words(`False None True and as assert async await break class continue def del elif else except finally
            for from global if import in is lambda nonlocal not or pass raise return try while with yield match case`)],
        ["builtin", words(`print len range int str float bool list dict set tuple open isinstance super self cls type
            enumerate zip map filter sorted sum min max any all repr input iter next abs round bytes object Exception
            ValueError TypeError KeyError OSError`)],
        ["function", FUNC],
        ["number", NUMBER],
        ["type", TYPE],
    ];

    const JS_KEYWORDS = `async await break case catch class const continue debugger default delete do else export extends
        finally for from function if import in instanceof let new of return static super switch this throw try typeof var
        void while with yield null undefined true false interface type enum implements private public protected readonly
        namespace declare abstract as is keyof satisfies`;
    LANGS.javascript = [
        ["comment", "//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["string", `${DQ}|${SQ}|${BT}`],
        ["keyword", words(JS_KEYWORDS)],
        ["builtin", words("console window document Math JSON Promise Array Object String Number Boolean Map Set Date RegExp Error fetch require module process")],
        ["function", FUNC],
        ["number", NUMBER],
        ["type", TYPE],
    ];

    LANGS.rust = [
        ["comment", "//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["string", `r#*"[\\s\\S]*?"#*|b?${DQ}`],
        ["string", "b?'(?:[^'\\\\\\n]|\\\\.)'"],
        ["meta", "#!?\\[[^\\]\\n]*\\]"],
        ["keyword", words(`as async await break const continue crate dyn else enum extern false fn for if impl in let loop match
            mod move mut pub ref return self Self static struct super trait true type unsafe use where while`)],
        ["builtin", words("Some None Ok Err Vec String Option Result Box Rc Arc HashMap HashSet println print format vec panic assert i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str")],
        ["function", FUNC],
        ["macro", "\\b[a-z_][a-z0-9_]*!"],
        ["number", NUMBER],
        ["type", TYPE],
    ];

    LANGS.go = [
        ["comment", "//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["string", `${DQ}|${BT}|${SQ}`],
        ["keyword", words(`break case chan const continue default defer else fallthrough for func go goto if import interface map
            package range return select struct switch type var nil true false`)],
        ["builtin", words("string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 float32 float64 bool byte rune error any len cap make new append copy delete panic recover print println")],
        ["function", FUNC],
        ["number", NUMBER],
        ["type", TYPE],
    ];

    const C_LIKE = (keywords, builtins, extra = []) => [
        ["comment", "//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["meta", "^[ \\t]*#[a-z]+[^\\n]*"],
        ["string", `${DQ}|${SQ}`],
        ...extra,
        ["keyword", words(keywords)],
        ["builtin", words(builtins)],
        ["function", FUNC],
        ["number", NUMBER],
        ["type", TYPE],
    ];
    LANGS.c = C_LIKE(
        `auto break case const continue default do else enum extern for goto if inline register return sizeof static struct switch
         typedef union volatile while NULL true false class namespace template typename public private protected virtual override
         new delete this using try catch throw nullptr constexpr noexcept operator final explicit`,
        "int char short long float double void unsigned signed bool size_t uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t string vector map std printf malloc free");
    LANGS.java = C_LIKE(
        `abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally
         float for goto if implements import instanceof int interface long native new package private protected public return short
         static strictfp super switch synchronized this throw throws transient try void volatile while var record sealed permits
         null true false fun val when object companion data override lateinit suspend internal namespace using is as in out
         string readonly async await foreach get set value func let guard defer extension protocol init`,
        "String System Math List Map Set Optional Integer Long Double Boolean Console Task Int Unit Any Array");
    LANGS.php = C_LIKE(
        `abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty
         enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global if implements
         include include_once instanceof interface isset list match namespace new or print private protected public readonly
         require require_once return static switch throw trait try unset use var while yield null true false`,
        "echo print_r var_dump strlen count array_map array_filter json_encode json_decode",
        [["variable", "\\$[A-Za-z_][A-Za-z0-9_]*"]]);

    LANGS.shell = [
        ["comment", "(?:^|\\s)#[^\\n]*"],
        ["string", `${DQ}|${SQ}`],
        ["variable", "\\$(?:\\{[^}\\n]*\\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@#?!*$-])"],
        ["keyword", words("if then else elif fi for while until do done case esac in function select time return exit break continue local export readonly declare unset set shift trap source")],
        ["builtin", words("echo printf cd ls cat grep sed awk find sudo chmod chown mkdir rm cp mv curl wget git docker systemctl test read eval exec xargs sort uniq head tail tee")],
        ["number", NUMBER],
        ["flag", "(?<=\\s)--?[A-Za-z][\\w-]*"],
    ];

    LANGS.sql = [
        ["comment", "--[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["string", SQ],
        ["keyword", "\\b(?:(?:select|from|where|and|or|not|insert|into|values|update|set|delete|create|table|alter|drop|index|view|join|inner|left|right|full|outer|on|group|by|order|having|limit|offset|as|distinct|union|all|case|when|then|else|end|null|is|in|like|between|exists|primary|key|foreign|references|unique|default|check|constraint|begin|commit|rollback|returning|with|asc|desc|count|sum|avg|min|max))\\b"],
        ["number", NUMBER],
        ["type", "\\b(?:int|integer|bigint|smallint|text|varchar|char|boolean|bool|date|timestamp|timestamptz|numeric|decimal|float|double|uuid|json|jsonb|serial)\\b"],
    ];
    // SQL est insensible à la casse.
    const CASE_INSENSITIVE = new Set(["sql"]);

    LANGS.json = [
        ["property", `${DQ}(?=\\s*:)`],
        ["string", DQ],
        ["keyword", "\\b(?:true|false|null)\\b"],
        ["number", "-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?"],
    ];

    LANGS.yaml = [
        ["comment", "#[^\\n]*"],
        ["property", "^[ \\t-]*[A-Za-z0-9_.\\-\"']+(?=\\s*:)"],
        ["string", `${DQ}|${SQ}`],
        ["keyword", "\\b(?:true|false|null|yes|no)\\b"],
        ["number", NUMBER],
    ];

    LANGS.css = [
        ["comment", "/\\*[\\s\\S]*?(?:\\*/|$)"],
        ["string", `${DQ}|${SQ}`],
        ["meta", "@[a-zA-Z-]+"],
        ["property", "[a-zA-Z-]+(?=\\s*:)"],
        ["number", "-?\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?"],
        ["variable", "--[a-zA-Z0-9-]+|#[0-9a-fA-F]{3,8}\\b"],
        ["type", "[.#][A-Za-z_][\\w-]*"],
    ];

    LANGS.html = [
        ["comment", "<!--[\\s\\S]*?(?:-->|$)"],
        ["tag", "</?[A-Za-z][\\w:-]*|/?>"],
        ["string", `${DQ}|${SQ}`],
        ["property", "\\b[A-Za-z_:][\\w:.-]*(?=\\s*=)"],
        ["meta", "&[a-zA-Z#0-9]+;"],
    ];

    LANGS.ruby = [
        ["comment", "#[^\\n]*"],
        ["string", `${DQ}|${SQ}`],
        ["variable", "[@$]{1,2}[A-Za-z_]\\w*|:[A-Za-z_]\\w*"],
        ["keyword", words("alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_accessor")],
        ["function", FUNC],
        ["number", NUMBER],
        ["type", TYPE],
    ];

    LANGS.lua = [
        ["comment", "--\\[\\[[\\s\\S]*?(?:\\]\\]|$)|--[^\\n]*"],
        ["string", `${DQ}|${SQ}`],
        ["keyword", words("and break do else elseif end false for function goto if in local nil not or repeat return then true until while")],
        ["builtin", words("print pairs ipairs require table string math os io type tostring tonumber self")],
        ["function", FUNC],
        ["number", NUMBER],
    ];

    const ALIASES = {
        py: "python", python3: "python", py3: "python",
        js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", ts: "javascript", tsx: "javascript",
        typescript: "javascript", node: "javascript", rs: "rust",
        golang: "go", h: "c", cc: "c", cpp: "c", cxx: "c", hpp: "c", "c++": "c", cs: "c", csharp: "c", "c#": "c",
        kt: "java", kotlin: "java", scala: "java", swift: "java", zig: "c",
        sh: "shell", bash: "shell", zsh: "shell", console: "shell", shell: "shell",
        yml: "yaml", htm: "html", xml: "html", svg: "html", jsonc: "json", rb: "ruby",
        postgresql: "sql", mysql: "sql", sqlite: "sql", plsql: "sql",
    };

    const compiled = new Map();

    function compile(name) {
        if (compiled.has(name)) return compiled.get(name);
        const rules = LANGS[name];
        if (!rules) return null;
        const flags = "gm" + (CASE_INSENSITIVE.has(name) ? "i" : "");
        let regex;
        try {
            regex = new RegExp(rules.map((r) => `(${r[1]})`).join("|"), flags);
        } catch {
            regex = null; // moteur sans lookbehind, par exemple : texte brut
        }
        const entry = regex ? { regex, classes: rules.map((r) => r[0]) } : null;
        compiled.set(name, entry);
        return entry;
    }

    function resolve(language) {
        const key = String(language || "").trim().toLowerCase();
        return LANGS[key] ? key : (ALIASES[key] || null);
    }

    function render(code, language) {
        const fragment = document.createDocumentFragment();
        const text = String(code == null ? "" : code);
        const name = resolve(language);
        const entry = name && text.length <= MAX_CHARS ? compile(name) : null;

        if (!entry) {
            fragment.append(document.createTextNode(text));
            return fragment;
        }

        const { regex, classes } = entry;
        regex.lastIndex = 0;
        let last = 0;
        let match;

        while ((match = regex.exec(text)) !== null) {
            if (match[0] === "") {
                regex.lastIndex += 1; // jamais de boucle infinie sur une correspondance vide
                continue;
            }
            if (match.index > last) fragment.append(document.createTextNode(text.slice(last, match.index)));

            let group = 1;
            while (group < match.length && match[group] === undefined) group += 1;
            const span = document.createElement("span");
            span.className = `tok-${classes[group - 1] || "plain"}`;
            span.textContent = match[0];
            fragment.append(span);
            last = match.index + match[0].length;
        }

        if (last < text.length) fragment.append(document.createTextNode(text.slice(last)));
        return fragment;
    }

    ICC.highlight = { render, resolve, languages: () => Object.keys(LANGS) };
})();
