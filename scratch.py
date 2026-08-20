# scratch.py
# 1. Custom Module Import
import helper


# 2. Function with a Default Argument
def greet(name: str, greeting: str = "Hello") -> str:
    return f"{greeting}, {name}!"


# 3. Class with __init__ and One Method
class DataStore:

    def __init__(self, raw_items: list[str]):
        self.items = raw_items

    def clean_and_count(self) -> int:
        self.items = [helper.format_title(item) for item in self.items]
        return len(self.items)


# Main Execution Block
if __name__ == "__main__":
    # Exercising Function with Default Argument
    print(greet("Alice"))
    print(greet("Bob", greeting="Welcome"))

    # 4. Set for Deduplication
    raw_tags = ["api", "python", "fastapi", "python", "API", "api"]
    unique_tags = set(raw_tags)
    print(f"Deduplicated tags (set): {unique_tags}")

    # 5. List Comprehension
    # Filtering and standardizing unique tags
    clean_tags = [t.lower() for t in unique_tags if len(t) > 2]
    print(f"Clean tags (list comp): {clean_tags}")

    # 6. Dict Comprehension
    # Mapping tag to its character length
    tag_lengths = {tag: len(tag) for tag in clean_tags}
    print(f"Tag lengths (dict comp): {tag_lengths}")

    # Exercising Custom Module and Class
    store = DataStore(["  server  ", "client ", "router"])
    count = store.clean_and_count()
    print(f"Processed {count} items: {store.items}")

    # 7. try/except/finally Catching a Specific Exception
    target_key = "database"
    try:
        print(f"Looking up '{target_key}'...")
        val = tag_lengths[target_key]
        print(f"Value: {val}")
    except KeyError as exc:
        print(f"Handled expected error: Key {exc} does not exist in dictionary.")
    finally:
        print("Execution of dictionary lookup block completed.")
