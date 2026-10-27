# Random Point Selection (QGIS 3 plugin)

Randomly selects a specified **number** or **percentage** of the points of a point layer.

**Processing Toolbox → Random Point Selection → Random selection → Random Point Selection**

| Parameter | Notes |
|---|---|
| Input points | Point layer. To sample only some features, select them and tick "Selected features only" |
| Selection method | Number of points, or Percentage of points |
| Number of points | Used with the number method; if larger than the layer, all points are selected |
| Percentage of points | Used with the percentage method (0-100); rounded to the nearest point, at least 1 if > 0 |
| Random seed | Optional - the same seed on the same data gives the same selection |
| Selected points | Output layer with all original attributes |
| Not selected points | Optional second output with the remaining points |

Sampling is without replacement and uniform. Each feature is one unit
(a multipoint feature is kept or dropped as a whole).
The algorithm also returns the number of selected points for use in models.

## Install
`Plugins → Manage and Install Plugins → Install from ZIP`, choose `random_point_selection.zip`, tick the plugin.
Requires QGIS ≥ 3.16. Edit `author`, `email` and URLs in `metadata.txt` before publishing.
