"""Example presentation matching Kolobok's Yomitan styles.css selectors."""

EXAMPLE_CSS = '''div[data-sc-content="example-sentence"] {
    border-radius: 0.4rem;
    border-style: none none none solid;
    border-width: calc(3em / var(--font-size-no-units, 14));
    margin-bottom: 0.5rem;
    margin-top: 0.5rem;
    padding: 0.5rem;
    width: fit-content;
    border-color: var(--text-color, var(--fg, #333));
    background-color: color-mix(in srgb, var(--text-color, var(--fg, #333)) 5%, transparent);
}
div[data-sc-content="example-sentence-a"] {
    font-size: 1.3em;
}
div[data-sc-content="example-sentence-b"] {
    font-size: 0.8em;
}
span[data-sc-content="example-keyword"] {
    color: color-mix(in srgb, lime, var(--text-color, var(--fg, #333)));
}
'''
