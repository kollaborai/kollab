# Custom Themes

Place your custom theme JSON files in this directory to use them in Kollab.

## Included Themes

| Theme | Description | Colors |
|-------|-------------|--------|
| `spring` | Fresh greens and floral colors | Greens, teals |
| `summer` | Warm yellows and bright oranges | Gold, amber |
| `autumn` | Warm oranges, reds, and browns | Rust, copper |
| `winter` | Cool blues and whites | Ice blues, frost |

## Theme Format

Copy any seasonal theme as a template and modify the colors.

```json
{
    "name": "mytheme",
    "primary": [[r, g, b], ...],      // Main accent gradient (3-5 colors)
    "secondary": [[r, g, b], ...],    // Info boxes, borders (3-5 colors)
    "response_bg": [[r, g, b], ...],  // AI response background (3 colors)
    "input_bg": [[r, g, b], ...],     // Input box background (3 colors)
    "dark": [[r, g, b], ...],         // Dark backgrounds (3 colors)
    "user_tag": [r, g, b],            // User message tag (solid RGB)
    "ai_tag": [r, g, b],              // AI message tag (solid RGB)
    "tool_tag": [r, g, b],            // Tool call tag (solid RGB)
    "thinking_tag": [r, g, b],        // Thinking tag (solid RGB)
    "success": [[r, g, b], ...],      // Success messages (3 colors)
    "error": [[r, g, b], ...],        // Error messages (3 colors)
    "warning": [[r, g, b], ...],      // Warning messages (3 colors)
    "text": [r, g, b],                // Main text color (RGB)
    "text_dim": [r, g, b]             // Dimmed text color (RGB)
}
```

## Tips

- More colors in gradients = smoother transitions
- RGB values range from 0-255
- Use `/config` to select your theme after creating the file
- Built-in themes: lime, ocean, sunset, mono, dark
