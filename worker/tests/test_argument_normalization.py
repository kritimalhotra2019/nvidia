"""Loose but legal argument spellings are repaired deterministically and reported, never refused.

27 Oct 2026: a 9B model spent fourteen rounds on `agg: value_counts`, `agg: count_distinct`, the same
text columns in `columns` and `categorical_columns`, and a 50-category cap. None of these is a wrong
request; they are spellings of a right one.
"""
import pandas as pd
import pytest

from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments


def setup(tmp_path, frame):
    p = Project(tmp_path / 'project', AllowList()); ctx = JobContext(p, lambda *a: None, lambda: False)
    path = tmp_path / 'cases.csv'; frame.to_csv(path, index=False); p.allow.allow(path)
    return ctx, registry.call('import_dataset', {'path': str(path)}, ctx)['dataset_id']


def cases(n=120):
    results = ['Granted', 'Dismissed', 'Remanded'] * (n // 3)
    return pd.DataFrame({'case_ref': [f'C{i:04d}' for i in range(n)],
                         'ap_result': results,
                         'court': ['High Court' if i % 4 else 'District Court' for i in range(n)],
                         'decision_type': [f'Type {i % 60}' for i in range(n)],          # 60 categories, not an id
                         'note': [f'free text {i}' for i in range(n)],                    # identifier-like text
                         'years_pending': [(i % 9) + 1 for i in range(n)]})


def test_aggregate_accepts_alias_spellings_and_reports_them(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('aggregate', {'dataset_id': ds, 'metrics': [{'column': 'ap_result', 'agg': 'count_distinct'}, {'column': 'years_pending', 'agg': 'Average'}, {'column': 'years_pending', 'agg': 'SD'}]}, ctx)
    row = dict(zip(r['columns'], r['rows'][0])) if 'columns' in r else None
    assert 'nunique_ap_result' in r['summary_text'] and 'mean_years_pending' in r['summary_text'] and 'std_years_pending' in r['summary_text']
    assert any('count_distinct' in n and 'nunique' in n for n in r['argument_notes'])
    assert 'Argument adjustments' in r['summary_text']


def test_aggregate_value_counts_becomes_a_grouped_count(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('aggregate', {'dataset_id': ds, 'metrics': [{'column': 'ap_result', 'agg': 'value_counts'}]}, ctx)
    assert '3 groups by ap_result' in r['summary_text'] and 'count_ap_result' in r['summary_text']
    assert any('grouped by ap_result' in n for n in r['argument_notes'])


def test_aggregate_two_frequency_columns_fall_back_to_distinct_counts(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('aggregate', {'dataset_id': ds, 'metrics': [{'column': 'ap_result', 'agg': 'value_counts'}, {'column': 'court', 'agg': 'frequency'}]}, ctx)
    assert 'nunique_ap_result' in r['summary_text'] and 'nunique_court' in r['summary_text']
    assert any('summarize_research_data' in n for n in r['argument_notes'])


def test_aggregate_still_refuses_an_unknown_metric(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    with pytest.raises(InvalidArguments, match='must be one of'):
        registry.call('aggregate', {'dataset_id': ds, 'metrics': [{'column': 'ap_result', 'agg': 'kurtosis'}]}, ctx)


def test_summarize_routes_overlapping_and_text_columns_by_their_values(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('summarize_research_data', {'dataset_id': ds, 'charts': False,
                      'columns': ['ap_result', 'court', 'years_pending'], 'categorical_columns': ['ap_result', 'court', 'years_pending']}, ctx)
    assert r['computed_measures'] == ['years_pending']
    assert {f['variable'] for f in r['frequencies']} == {'ap_result', 'court'}
    assert any('ap_result holds text' in n for n in r['argument_notes']) and any('years_pending holds numbers' in n for n in r['argument_notes'])
    granted = next(f for f in r['frequencies'] if f['variable'] == 'ap_result' and f['category'] == 'Granted')
    assert granted['n'] == 40 and granted['nonmissing_denominator'] == 120


def test_summarize_pools_long_category_lists_and_skips_identifier_like_text(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('summarize_research_data', {'dataset_id': ds, 'charts': False, 'categorical_columns': ['decision_type', 'note', 'ap_result']}, ctx)
    kinds = {f['variable'] for f in r['frequencies']}
    assert 'note' not in kinds and 'decision_type' in kinds
    decision = [f for f in r['frequencies'] if f['variable'] == 'decision_type']
    other = next(f for f in decision if f['category'] == 'Other (pooled categories)')
    assert len(decision) == 21 and other['n'] == 120 - sum(f['n'] for f in decision if f is not other)
    assert sum(f['n'] for f in decision) == 120
    assert any('60 categories' in n and 'pooled as Other' in n for n in r['argument_notes'])
    assert any('note has 120 distinct values' in n for n in r['argument_notes'])


def test_summarize_trims_chart_columns_to_four_and_merges_repeats(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('summarize_research_data', {'dataset_id': ds, 'charts': True,
                      'categorical_columns': ['ap_result', 'court', 'ap_result'],
                      'chart_columns': ['ap_result', 'court', 'years_pending', 'decision_type', 'note', 'case_ref']}, ctx)
    assert any('limited to four' in n for n in r['argument_notes']) and any('Repeated names' in n for n in r['argument_notes'])
    assert r['chart_measures'] == ['ap_result', 'court']
    assert any(n.startswith('Not charted') for n in r['argument_notes'])


def test_summarize_refuses_when_nothing_is_summarisable(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    with pytest.raises(InvalidArguments, match='None of the selected columns'):
        registry.call('summarize_research_data', {'dataset_id': ds, 'charts': False, 'categorical_columns': ['note']}, ctx)


def test_aggregate_count_by_group_carries_a_share_of_total(tmp_path):
    ctx, ds = setup(tmp_path, cases())
    r = registry.call('aggregate', {'dataset_id': ds, 'group_by': ['ap_result'], 'metrics': [{'column': 'case_ref', 'agg': 'count'}]}, ctx)
    assert r['table']['columns'] == ['ap_result', 'count_case_ref', 'share_pct_case_ref']
    by = {row[0]: row for row in r['table']['rows']}
    assert by['Granted'][1] == 40 and by['Granted'][2] == pytest.approx(33.33, abs=0.01)
    assert sum(row[2] for row in r['table']['rows']) == pytest.approx(100, abs=0.05)
    assert 'share_pct_case_ref: each group' in r['summary_text'] and r['metrics'] == ['count_case_ref']
    assert 'share_pct' not in registry.call('aggregate', {'dataset_id': ds, 'metrics': [{'column': 'case_ref', 'agg': 'count'}]}, ctx)['summary_text']


def test_find_insights_never_draws_a_trend_over_a_date_of_birth(tmp_path):
    years = [1940 + (i % 60) for i in range(240)]
    frame = pd.DataFrame({'head_dob': pd.to_datetime([f'{y}-06-15' for y in years]),
                          'survey_date': pd.to_datetime(['2024-01-01'] * 240),
                          'food_insecure_days': [max(0, int((2000 - y) / 6)) for y in years],
                          'district': ['North', 'South'] * 120})
    ctx, ds = setup(tmp_path, frame)
    r = registry.call('find_insights', {'dataset_id': ds, 'charts': False}, ctx)
    assert not any(f['kind'] == 'trend' and f.get('over') == 'head_dob' for f in r['findings']), [f['title'] for f in r['findings']]
