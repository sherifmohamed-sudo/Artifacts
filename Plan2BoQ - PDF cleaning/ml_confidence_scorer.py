#!/usr/bin/env python3
"""
Rule-Based Classifier with ML-Style Confidence Scores
======================================================

Fast, lightweight approach that:
1. Uses pattern matching (like current system)
2. Adds confidence scores based on pattern strength
3. Provides detailed reasoning for each decision
4. Completes in under 1 minute

This gives you "ML-style" outputs (confidence scores, detailed reasoning)
without requiring large pre-trained models.
"""

import fitz
import sys
from typing import Dict, List, Tuple
import re


class ConfidenceScorer:
    """
    Enhanced rule-based classifier with confidence scoring.
    """
    
    def __init__(self):
        """Initialize pattern matcher with confidence weights."""
        
        # Define patterns with confidence weights
        self.removal_patterns = {
            # High confidence removals (1.0 = 100% certain)
            'furniture': {
                'patterns': ['furn', 'mobilier', 'chair', 'table', 'desk', 'sofa'],
                'confidence': 0.95
            },
            'stairs': {
                'patterns': ['stair', 'steps', 'handrail', 'escalier'],
                'confidence': 0.95
            },
            'lifts': {
                'patterns': ['lift', 'elevator', 'noyaux'],
                'confidence': 0.95
            },
            'grid': {
                'patterns': ['grid line', '_grid', 'axes grid', '_axs'],
                'confidence': 0.90
            },
            'dimensions': {
                # Only remove explicit grid/column/lift dimension layers
                # Generic 'A-dimensions' layers are kept — they hold door/window values
                'patterns': ['grid dim', 'column-dim', 'lift-dim', 'grid_dim'],
                'confidence': 0.90
            },
            'hatching': {
                'patterns': ['hatch', 'hachures', '-hat-', '_hatch'],
                'confidence': 0.95
            },
            'road': {
                'patterns': ['road', 'a-road'],
                'confidence': 0.95
            },
            'landscape': {
                'patterns': ['landscape', 'tree', 'bench', 'l-pl-'],
                'confidence': 0.90
            },
            'parking': {
                'patterns': ['parking', 'arrow'],
                'confidence': 0.85
            },
            'annotations': {
                'patterns': ['cloud', 'stamp', 'tblock', '-tag-box'],
                'confidence': 0.85
            },
            'mep': {
                'patterns': ['mep', 'duct', 'mechanical'],
                'confidence': 0.80
            },
            'tags': {
                'patterns': ['elev. tag', 'sec. tag'],
                'confidence': 0.80
            },
        }
        
        # Keep patterns (highest priority)
        self.keep_patterns = {
            'window': {
                # Window checked first to prevent 'd-tag' substring matching 'wind-tag' as door
                'patterns': ['window', 'wind', 'win-', 'glaz', 'a-win', 'wind-tag', 'w-tag'],
                'confidence': 1.0  # 100% keep
            },
            'door': {
                'patterns': ['door', 'dr-', 'a-door', 'a_a_door', 'door-tag', '-d-tag'],
                'confidence': 1.0  # 100% keep
            },
            'wall': {
                'patterns': ['wall'],
                'confidence': 1.0  # 100% keep
            },
            'symbol': {
                'patterns': ['symb-misc', 'symbol'],
                'confidence': 0.90  # High confidence keep
            },
        }
    
    def analyze_layer_name(
        self,
        layer_name: str
    ) -> Tuple[bool, str, float, Dict]:
        """
        Analyze layer name and provide decision with confidence.
        
        Args:
            layer_name: Layer name string
            
        Returns:
            (should_remove, reason, confidence, details_dict)
        """
        nl = layer_name.lower()
        
        # STEP 1: Check KEEP patterns (highest priority)
        for category, config in self.keep_patterns.items():
            for pattern in config['patterns']:
                if pattern in nl:
                    return False, f"Essential: {category}", config['confidence'], {
                        'category': category,
                        'matched_pattern': pattern,
                        'action': 'KEEP',
                        'reasoning': f"Matched essential pattern: '{pattern}'"
                    }
        
        # STEP 2: Check REMOVAL patterns
        best_match = None
        best_confidence = 0.0
        
        for category, config in self.removal_patterns.items():
            for pattern in config['patterns']:
                if pattern in nl:
                    if config['confidence'] > best_confidence:
                        best_confidence = config['confidence']
                        best_match = (category, pattern, config['confidence'])
        
        if best_match:
            category, pattern, confidence = best_match
            return True, f"Unwanted: {category}", confidence, {
                'category': category,
                'matched_pattern': pattern,
                'action': 'REMOVE',
                'reasoning': f"Matched removal pattern: '{pattern}'"
            }
        
        # STEP 3: DEFAULT = KEEP (when uncertain)
        # Calculate uncertainty score
        uncertainty = 0.3  # Low confidence when no patterns match
        
        return False, "No clear pattern, keeping by default", uncertainty, {
            'category': 'unknown',
            'matched_pattern': None,
            'action': 'KEEP',
            'reasoning': "No matching patterns, applying default keep rule"
        }
    
    def analyze_pdf(
        self,
        pdf_path: str,
        min_confidence_display: float = 0.0
    ) -> Dict:
        """
        Analyze all layers in PDF.
        
        Args:
            pdf_path: Path to PDF
            min_confidence_display: Only display decisions above this confidence
            
        Returns:
            Analysis results
        """
        doc = fitz.open(pdf_path)
        layers = doc.layer_ui_configs()
        doc.close()
        
        results = {
            'remove': [],
            'keep': [],
            'total_layers': len(layers),
            'high_confidence_remove': 0,
            'high_confidence_keep': 0,
            'uncertain': 0
        }
        
        print(f"\nAnalyzing {len(layers)} layers...")
        print("-" * 80)
        
        for layer in layers:
            layer_name = layer.get('text', '')
            layer_num = layer.get('number')
            
            should_remove, reason, confidence, details = self.analyze_layer_name(layer_name)
            
            result = {
                'number': layer_num,
                'name': layer_name,
                'should_remove': should_remove,
                'reason': reason,
                'confidence': confidence,
                'details': details
            }
            
            if should_remove:
                results['remove'].append(result)
                if confidence >= 0.8:
                    results['high_confidence_remove'] += 1
            else:
                results['keep'].append(result)
                if confidence >= 0.8:
                    results['high_confidence_keep'] += 1
            
            if confidence < 0.5:
                results['uncertain'] += 1
        
        return results


def generate_report(results: Dict, output_path: str):
    """Generate detailed analysis report."""
    with open(output_path, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("ML-STYLE CONFIDENCE-SCORED LAYER ANALYSIS\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"Total layers analyzed: {results['total_layers']}\n")
        f.write(f"Recommended to REMOVE: {len(results['remove'])}\n")
        f.write(f"Recommended to KEEP: {len(results['keep'])}\n\n")
        
        f.write(f"Confidence breakdown:\n")
        f.write(f"  High confidence remove (≥0.8): {results['high_confidence_remove']}\n")
        f.write(f"  High confidence keep (≥0.8): {results['high_confidence_keep']}\n")
        f.write(f"  Uncertain (<0.5): {results['uncertain']}\n\n")
        
        # Sort by confidence
        remove_sorted = sorted(results['remove'], key=lambda x: x['confidence'], reverse=True)
        keep_sorted = sorted(results['keep'], key=lambda x: x['confidence'], reverse=True)
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("LAYERS TO REMOVE (sorted by confidence)\n")
        f.write("=" * 80 + "\n\n")
        
        for r in remove_sorted:
            conf_percent = r['confidence'] * 100
            f.write(f"[{conf_percent:5.1f}%] oc{r['number']}: {r['name']}\n")
            f.write(f"         Reason: {r['reason']}\n")
            f.write(f"         Details: {r['details']['reasoning']}\n\n")
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("LAYERS TO KEEP (sorted by confidence)\n")
        f.write("=" * 80 + "\n\n")
        
        # Show high confidence keeps
        high_conf_keeps = [k for k in keep_sorted if k['confidence'] >= 0.8]
        f.write(f"High confidence keeps ({len(high_conf_keeps)}):\n\n")
        for r in high_conf_keeps:
            conf_percent = r['confidence'] * 100
            f.write(f"[{conf_percent:5.1f}%] oc{r['number']}: {r['name']}\n")
            f.write(f"         Reason: {r['reason']}\n\n")
        
        # Show uncertain keeps
        uncertain_keeps = [k for k in keep_sorted if k['confidence'] < 0.5]
        if uncertain_keeps:
            f.write(f"\nUncertain keeps (confidence < 0.5) - REVIEW THESE:\n\n")
            for r in uncertain_keeps[:30]:
                conf_percent = r['confidence'] * 100
                f.write(f"[{conf_percent:5.1f}%] oc{r['number']}: {r['name']}\n")
                f.write(f"         Reason: {r['reason']}\n\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ml_confidence_scorer.py <pdf_path>")
        print("\nExample:")
        print("  python3 ml_confidence_scorer.py input.pdf")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    print("=" * 80)
    print("RULE-BASED CLASSIFIER WITH CONFIDENCE SCORES")
    print("=" * 80)
    print(f"\nPDF: {pdf_path}")
    
    # Initialize scorer
    scorer = ConfidenceScorer()
    
    # Analyze
    results = scorer.analyze_pdf(pdf_path)
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Total layers: {results['total_layers']}")
    print(f"  Recommend REMOVE: {len(results['remove'])} layers")
    print(f"  Recommend KEEP: {len(results['keep'])} layers")
    print(f"  Removal rate: {len(results['remove']) / results['total_layers'] * 100:.1f}%")
    
    print(f"\n  Confidence breakdown:")
    print(f"    High confidence remove (≥80%): {results['high_confidence_remove']}")
    print(f"    High confidence keep (≥80%): {results['high_confidence_keep']}")
    print(f"    Uncertain (<50%): {results['uncertain']}")
    
    # Show examples
    print(f"\n  Example removals (high confidence):")
    high_conf_remove = [r for r in results['remove'] if r['confidence'] >= 0.85]
    for r in high_conf_remove[:5]:
        print(f"    • [{r['confidence']*100:.0f}%] {r['name'][:60]}")
        print(f"      └─ {r['reason']}")
    
    print(f"\n  Example keeps (essential elements):")
    essential = [k for k in results['keep'] if k['confidence'] >= 0.95]
    for r in essential[:5]:
        print(f"    • [{r['confidence']*100:.0f}%] {r['name'][:60]}")
        print(f"      └─ {r['reason']}")
    
    # Generate report
    generate_report(results, "ml_confidence_report.txt")
    
    print(f"\n✅ Detailed report saved: ml_confidence_report.txt")
    print("=" * 80)
