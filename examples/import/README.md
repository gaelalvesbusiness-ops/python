# Synthetic import examples

These files are **synthetic demonstration data**. The numbers were chosen by hand to exercise
the import; they are not verified segments and must never be imported into a production
database.

- `synthetic_verified_numbers.txt`: one number per line, with one repeated number.
- `synthetic_verified_numbers.csv`: a `Number` column plus a free `Comment` column, with one
  repeated number, one number that is also in the TXT file and one (number 2) that the
  demonstration also stores as a simulated website record.

`python demo_import.py` imports both into a temporary database through `main.py` and checks the
results. Real imports must use a file from a trusted, authorized source.
