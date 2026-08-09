"""
Quick analysis of Gemini OSM experiment results — agreement rates between
Gemini and the Stage 1 HSV classifier for roof colour, material, shape, and pitch.

Usage:
    python analyse_gemini_results.py [suburb_name]
"""
import sys
import pandas as pd
from pathlib import Path

EXPERIMENT_DIR = Path("data/output/experiments")


def analyse(suburb_name: str) -> pd.DataFrame:
    csv_path = EXPERIMENT_DIR / f"gemini_osm_stage1_{suburb_name.lower().replace(' ', '_')}.csv"
    if not csv_path.exists():
        print(f"Not found: {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} Gemini assessments for {suburb_name}")
    print()

    # ── QA summary ────────────────────────────────────────────────────────
    print("=== QA Actions ===")
    print(df["qa_action"].value_counts().to_string())
    print()

    # ── Roof colour agreement ─────────────────────────────────────────────
    # Compare Gemini roof_colour with stage1_roof_colour
    # Only for buildings where both have a known value (not 'other' or 'unknown')
    colour_mask = (
        df["roof_colour"].notna()
        & (df["roof_colour"] != "unknown")
        & (df["roof_colour"] != "other")
        & df["stage1_roof_colour"].notna()
        & (df["stage1_roof_colour"] != "other")
    )
    colour_df = df[colour_mask].copy()
    colour_agree = colour_df["roof_colour"] == colour_df["stage1_roof_colour"]

    print("=== Roof Colour Agreement ===")
    print(f"  Comparable buildings: {len(colour_df)}")
    print(f"  Agree: {colour_agree.sum()} ({colour_agree.mean()*100:.1f}%)")

    # Per-colour accuracy
    if len(colour_df) > 0:
        print("  By colour (Gemini / Stage1 = agree rate):")
        for colour in sorted(colour_df["stage1_roof_colour"].unique()):
            subset = colour_df[colour_df["stage1_roof_colour"] == colour]
            if len(subset) > 0:
                agree = (subset["roof_colour"] == colour).sum()
                print(f"    {colour}: {agree}/{len(subset)} ({agree/len(subset)*100:.0f}%)")
    print()

    # ── Roof material agreement ───────────────────────────────────────────
    material_mask = (
        df["roof_material"].notna()
        & (df["roof_material"] != "unknown")
        & (df["roof_material"] != "other")
        & df["stage1_roof_material"].notna()
        & (df["stage1_roof_material"] != "other")
    )
    material_df = df[material_mask].copy()
    material_agree = material_df["roof_material"] == material_df["stage1_roof_material"]

    print("=== Roof Material Agreement ===")
    print(f"  Comparable buildings: {len(material_df)}")
    print(f"  Agree: {material_agree.sum()} ({material_agree.mean()*100:.1f}%)")

    if len(material_df) > 0:
        print("  By material (Gemini / Stage1 = agree rate):")
        for mat in sorted(material_df["stage1_roof_material"].unique()):
            subset = material_df[material_df["stage1_roof_material"] == mat]
            if len(subset) > 0:
                agree = (subset["roof_material"] == mat).sum()
                print(f"    {mat}: {agree}/{len(subset)} ({agree/len(subset)*100:.0f}%)")
    print()

    # ── Roof shape coverage ───────────────────────────────────────────────
    # Stage 1 rarely has roof_shape — count how many Gemini fills in
    stage1_shape_known = df["stage1_roof_shape"].notna() & (df["stage1_roof_shape"] != "")
    gemini_shape_known = df["roof_shape"].notna() & (df["roof_shape"] != "unknown")
    print("=== Roof Shape Coverage ===")
    print(f"  Stage 1 has shape: {stage1_shape_known.sum()} ({stage1_shape_known.mean()*100:.1f}%)")
    print(f"  Gemini has shape:  {gemini_shape_known.sum()} ({gemini_shape_known.mean()*100:.1f}%)")
    print(f"  Gemini fills gaps: {(gemini_shape_known & ~stage1_shape_known).sum()} buildings")
    print("  Gemini shapes:", df.loc[gemini_shape_known, "roof_shape"].value_counts().to_dict())
    print()

    # ── Pitch comparison ──────────────────────────────────────────────────
    pitch_obs = df[df["pitch_observable"] == True]
    print("=== Pitch ===")
    print(f"  Pitch observable: {len(pitch_obs)} ({len(pitch_obs)/len(df)*100:.0f}%)")
    print("  Gemini pitch class:", df["pitch_class"].value_counts().to_dict())
    if len(pitch_obs) > 0:
        print(f"  Gemini pitch estimates (where observable):")
        print(f"    mean: {pitch_obs['pitch_deg_estimate'].mean():.1f} deg")
        print(f"    median: {pitch_obs['pitch_deg_estimate'].median():.1f} deg")
    print(f"  Stage 1 assumed pitch (mean): {df['stage1_pitch_deg'].mean():.1f} deg")
    print()

    # ── Confidence summary ────────────────────────────────────────────────
    print("=== Confidence Summary ===")
    print(f"  Mean Gemini confidence: {df['confidence'].mean():.2f}")
    print(f"  Mean QA score: {df['qa_score'].mean():.2f}")
    print(f"  High confidence (>=0.8): {(df['confidence'] >= 0.8).sum()} ({(df['confidence'] >= 0.8).mean()*100:.0f}%)")
    print(f"  Low confidence (<0.5): {(df['confidence'] < 0.5).sum()} ({(df['confidence'] < 0.5).mean()*100:.0f}%)")

    return df


if __name__ == "__main__":
    suburb = sys.argv[1] if len(sys.argv) > 1 else "clayton"
    analyse(suburb)
