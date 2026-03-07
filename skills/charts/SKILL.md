# Terminal Charts

Create bar and line charts in the terminal using termgraph.

## Bar Chart

```bash
# From inline data
echo -e "2020 50\n2021 75\n2022 100\n2023 125" | termgraph

# With title
echo -e "Jan 100\nFeb 150\nMar 200" | termgraph --title "Monthly Sales"

# Custom width
echo -e "A 50\nB 100\nC 75" | termgraph --width 50

# With colors
echo -e "Red 30\nBlue 50\nGreen 40" | termgraph --color green
```

## From File

Create a data file (space or tab separated):
```
# data.txt
January   100
February  150
March     200
April     175
```

Then:
```bash
termgraph data.txt
```

## Stacked Bar Chart

```bash
# Format: label value1 value2 value3
echo -e "2020 10 20 30\n2021 15 25 35\n2022 20 30 40" | termgraph --stacked
```

## Horizontal Line Chart

```bash
echo -e "Mon 10\nTue 15\nWed 8\nThu 20\nFri 12" | termgraph --suffix " orders"
```

## Options

- `--title "Title"` - Add title
- `--width N` - Chart width (default: 50)
- `--color {red,blue,green,magenta,yellow,cyan}` - Bar color
- `--stacked` - Stacked bars for multi-value data
- `--suffix " text"` - Add suffix to values
- `--no-labels` - Hide labels
- `--no-values` - Hide values
