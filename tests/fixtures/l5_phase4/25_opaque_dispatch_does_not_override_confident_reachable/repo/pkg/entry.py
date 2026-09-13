import pkg.target2 as target2
import pkg.dispatcher as dispatcher

if __name__ == "__main__":
    target2.vulnerable()
    dispatcher.dispatch("something")
