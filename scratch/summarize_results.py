import glob
import csv

csv_files = sorted(glob.glob('logs/*.csv'))

livebooks = {}

for f in csv_files:
    try:
        with open(f, 'r', encoding='utf-8') as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                if row.get('event') == 'result':
                    lb = row.get('livebook', 'Unknown')
                    lu = row.get('lu', '')
                    title = row.get('lu_title', '')
                    result = row.get('result', '')
                    attempt = row.get('attempt', '1')
                    key = (lb, lu, title)
                    # keep latest or best result
                    livebooks[key] = (result, attempt, f)
    except Exception as e:
        print(f"Error reading {f}: {e}")

total_quizzes = len(livebooks)
max_score_quizzes = 0
passed_quizzes = 0

by_course = {}

for (lb, lu, title), (res, att, logfile) in livebooks.items():
    if lb not in by_course:
        by_course[lb] = []
    by_course[lb].append((lu, title, res, att))
    
    # parse score
    res_clean = res.lower()
    if 'passed' in res_clean or '5/5' in res_clean or '9/9' in res_clean or '4/5' in res_clean:
        passed_quizzes += 1
    if ('5/5' in res_clean or '9/9' in res_clean or '4/4' in res_clean) and '4/5' not in res_clean:
        max_score_quizzes += 1

print("=" * 70)
print("KALVIUM SEMESTER 5 AUTOMATION - GRAND SUMMARY REPORT")
print("=" * 70)
print(f"Total Livebooks Completed: {len(by_course)}")
print(f"Total Unique Quizzes Automated: {total_quizzes}")
print(f"Perfect Max Scores (100%): {max_score_quizzes} ({max_score_quizzes/total_quizzes*100:.1f}%)")
print(f"Passing / Verified Quizzes: {passed_quizzes} ({passed_quizzes/total_quizzes*100:.1f}%)")
print("=" * 70)

for lb, items in sorted(by_course.items()):
    perfect_count = sum(1 for _, _, r, _ in items if ('5/5' in r or '9/9' in r) and '4/5' not in r)
    print(f"\n[{lb.upper()}] - {len(items)} Quizzes ({perfect_count}/{len(items)} Perfect)")
    for lu, title, res, att in sorted(items, key=lambda x: float(x[0]) if x[0].replace('.', '', 1).isdigit() else 999):
        print(f"  LU {lu:5} | {title[:40]:40} | {res}")
