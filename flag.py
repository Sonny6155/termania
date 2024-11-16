class Flag:
    # Hack to pass a bool flag by ref, used for threads
    def __init__(self, init_state: bool):
        self.state = init_state

