# The two marks that weren’t picked

Three candidates went into the homescreen-icon review; `c` (two overlapping
watchlists with a play button in the intersection) shipped as
`app/static/icons/favicon.svg`. These are the other two, kept so switching is a
copy rather than a redraw:

| file | what it is | background |
|---|---|---|
| `a-clapperboard.svg` | blue board on the app's panel grey — quietest | `#171a21` |
| `b-inverted-clapperboard.svg` | dark board on full-bleed accent blue | `#7aa0ff` |

To switch:

```sh
cp scripts/icon-alternates/a-clapperboard.svg app/static/icons/favicon.svg
# set bg= in scripts/make-icons.sh to that file's background colour first —
# it's what alpha removal composites onto
./scripts/make-icons.sh
```

Both are flat shapes with explicit fills and no `<text>`, so they survive being
resized to a 16px browser tab.
