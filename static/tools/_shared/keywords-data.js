/* 多语言关键字速查数据。
 *
 * 只放静态资料：每条包含语言、关键字、分类、一句话说明、可运行示例、注意点和官方文档。
 * 页面按关键字/说明/代码做本地搜索；本地没收录时再让 AI 补充。
 */
window.KEYWORD_DATA = [
  /* ------------------------------ Python ------------------------------ */
  {
    lang: 'python',
    keyword: 'yield',
    category: '语法',
    summary: '在函数里产出值，把函数变成生成器；调用时返回迭代器，按需计算。',
    code: `def count(n):
    for i in range(n):
        yield i

for x in count(3):
    print(x)`,
    note: '生成器函数调用时不会立刻执行，第一次 next() 才开始；适合大文件、流式处理和懒加载。',
    docs: 'https://docs.python.org/3/reference/expressions.html#yieldexpr'
  },
  {
    lang: 'python',
    keyword: 'async/await',
    category: '并发',
    summary: '用 async def 定义协程，用 await 等待异步操作，不阻塞事件循环。',
    code: `import asyncio

async def fetch():
    await asyncio.sleep(1)
    return "done"

print(asyncio.run(fetch()))`,
    note: 'await 只能出现在 async def 里；CPU 密集任务不要靠协程，应该用进程池或线程池。',
    docs: 'https://docs.python.org/3/library/asyncio-task.html'
  },
  {
    lang: 'python',
    keyword: 'decorator',
    category: '函数',
    summary: '接收一个函数并返回一个新函数，在不改原函数代码的前提下增加行为。',
    code: `import functools

def log(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("call", fn.__name__)
        return fn(*args, **kwargs)
    return wrapper

@log
def add(a, b):
    return a + b`,
    note: '装饰器本质是「函数套函数」；用 functools.wraps 保留原函数的名字和文档字符串。',
    docs: 'https://docs.python.org/3/glossary.html#term-decorator'
  },
  {
    lang: 'python',
    keyword: 'with',
    category: '语法',
    summary: '上下文管理器语法，离开代码块时自动释放文件、锁、连接等资源。',
    code: `with open("data.txt", encoding="utf-8") as f:
    for line in f:
        print(line.rstrip())`,
    note: '即使代码块里抛异常也会执行清理；自定义类实现 __enter__ 和 __exit__ 即可支持 with。',
    docs: 'https://docs.python.org/3/reference/compound_stmts.html#the-with-statement'
  },
  {
    lang: 'python',
    keyword: 'dataclass',
    category: '数据类',
    summary: '用装饰器自动生成 __init__、__repr__、__eq__ 等方法的轻量数据类。',
    code: `from dataclasses import dataclass

@dataclass
class User:
    name: str
    age: int = 0

print(User("Ann"))`,
    note: '可变默认值要用 field(default_factory=list)，不能直接写 []；需要不可变时加 frozen=True。',
    docs: 'https://docs.python.org/3/library/dataclasses.html'
  },

  /* -------------------------------- C++ ------------------------------- */
  {
    lang: 'cpp',
    keyword: 'RAII',
    category: '资源管理',
    summary: '资源获取即初始化：用对象生命周期管理文件、锁、内存等资源。',
    code: `#include <fstream>
#include <iostream>

int main() {
    std::ifstream in("data.txt");
    if (!in) return 1;
    std::string line;
    while (std::getline(in, line)) std::cout << line << "\\n";
}`,
    note: '对象离开作用域时析构函数自动释放资源；即使中途 return 或抛异常也不会泄漏。',
    docs: 'https://en.cppreference.com/w/cpp/language/raii'
  },
  {
    lang: 'cpp',
    keyword: 'template',
    category: '泛型',
    summary: '在编译期生成类型安全的泛型代码，函数模板和类模板都靠它。',
    code: `template <typename T>
T maxOf(T a, T b) {
    return a > b ? a : b;
}

int main() {
    return maxOf(3, 7);
}`,
    note: '模板定义通常放头文件；C++20 可用 concepts 约束类型，报错会比传统 SFINAE 清楚。',
    docs: 'https://en.cppreference.com/w/cpp/language/templates'
  },
  {
    lang: 'cpp',
    keyword: 'virtual',
    category: '面向对象',
    summary: '声明虚函数，让基类指针/引用在运行时调用到派生类的实现。',
    code: `struct Base {
    virtual void run() { }
    virtual ~Base() = default;
};

struct Derived : Base {
    void run() override { }
};`,
    note: '基类析构函数必须是 virtual，否则通过基类指针 delete 派生对象是未定义行为。',
    docs: 'https://en.cppreference.com/w/cpp/language/virtual'
  },
  {
    lang: 'cpp',
    keyword: 'constexpr',
    category: '编译期',
    summary: '告诉编译器这个函数或变量可以（也尽量）在编译期求值。',
    code: `constexpr int square(int x) { return x * x; }
static_assert(square(5) == 25);

int main() { return square(3); }`,
    note: 'C++20 起还有 consteval（必须编译期）和 constinit；不要把它当成运行期优化开关。',
    docs: 'https://en.cppreference.com/w/cpp/language/constexpr'
  },
  {
    lang: 'cpp',
    keyword: 'unique_ptr',
    category: '内存',
    summary: '独占所有权的智能指针，离开作用域自动 delete，不能拷贝只能移动。',
    code: `#include <memory>

int main() {
    auto p = std::make_unique<int>(42);
    return *p;
}`,
    note: '优先用 std::make_unique，避免裸 new；需要共享所有权时才考虑 shared_ptr。',
    docs: 'https://en.cppreference.com/w/cpp/memory/unique_ptr'
  },

  /* -------------------------------- Java ------------------------------ */
  {
    lang: 'java',
    keyword: 'interface',
    category: '面向对象',
    summary: '定义一组方法契约，类通过 implements 提供实现；Java 8+ 支持 default 方法。',
    code: `interface Greeter {
    String greet(String name);

    default String hello() {
        return "hello";
    }
}`,
    note: '接口不能有实例字段；一个类可以实现多个接口，这是 Java 实现多态的主要方式。',
    docs: 'https://docs.oracle.com/javase/tutorial/java/IandI/createinterface.html'
  },
  {
    lang: 'java',
    keyword: 'synchronized',
    category: '并发',
    summary: '用对象或类级别的锁保护临界区，保证同一时刻只有一个线程进入。',
    code: `class Counter {
    private int value;

    synchronized void inc() {
        value++;
    }
}`,
    note: '锁的是对象（实例方法）或 Class（静态方法）；不要锁 String/包装类型，也不要把长时间 IO 放进临界区。',
    docs: 'https://docs.oracle.com/javase/tutorial/essential/concurrency/syncmeth.html'
  },
  {
    lang: 'java',
    keyword: 'record',
    category: '数据类',
    summary: 'Java 16+ 的不可变数据载体，自动生成构造器、访问器、equals/hashCode/toString。',
    code: `record Point(int x, int y) { }

public class Main {
    public static void main(String[] args) {
        System.out.println(new Point(1, 2));
    }
}`,
    note: '适合 DTO/值对象；字段隐式 final，不能像普通类那样再声明实例字段。',
    docs: 'https://docs.oracle.com/en/java/javase/17/language/records.html'
  },
  {
    lang: 'java',
    keyword: 'Optional',
    category: '空值处理',
    summary: '显式表达「可能没有值」，减少空指针判断和 null 返回。',
    code: `import java.util.Optional;

Optional<String> name = Optional.ofNullable(null);
System.out.println(name.orElse("anonymous"));`,
    note: '不要用 Optional 做字段或方法参数；取值优先 orElse/ifPresent/map，避免直接 get()。',
    docs: 'https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/Optional.html'
  },
  {
    lang: 'java',
    keyword: 'Stream',
    category: '集合',
    summary: '对集合做声明式链式处理：filter、map、reduce、collect。',
    code: `import java.util.List;

List<Integer> nums = List.of(1, 2, 3, 4);
int sum = nums.stream()
    .filter(n -> n % 2 == 0)
    .mapToInt(Integer::intValue)
    .sum();`,
    note: '流只能消费一次；中间操作是惰性的，遇到终止操作才执行；并行流要确认线程安全。',
    docs: 'https://docs.oracle.com/javase/8/docs/api/java/util/stream/Stream.html'
  },

  /* ------------------------------- 前端 ------------------------------- */
  {
    lang: 'frontend',
    keyword: 'closure',
    category: '语言基础',
    summary: '内部函数持有外部作用域变量的引用，即使外部函数已经返回。',
    code: `function counter() {
  let n = 0;
  return () => ++n;
}

const next = counter();
console.log(next(), next());`,
    note: '常用于私有状态、防抖/节流；注意闭包会让变量常驻内存，不再需要时置空引用。',
    docs: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Closures'
  },
  {
    lang: 'frontend',
    keyword: 'event loop',
    category: '运行时',
    summary: 'JavaScript 的事件循环：同步代码跑完，先清微任务，再取宏任务。',
    code: `console.log("A");
setTimeout(() => console.log("B"), 0);
Promise.resolve().then(() => console.log("C"));
console.log("D");
// 输出：A D C B`,
    note: '长任务会阻塞渲染；Promise 属于微任务，setTimeout 属于宏任务。',
    docs: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Event_loop'
  },
  {
    lang: 'frontend',
    keyword: 'Promise',
    category: '异步',
    summary: '表示一个异步操作的最终结果，用 then/catch/finally 或 await 处理。',
    code: `fetch("/api/data")
  .then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then((data) => console.log(data))
  .catch((err) => console.error(err));`,
    note: 'fetch 只有网络错误才 reject，4xx/5xx 不会；必须自己检查 r.ok。',
    docs: 'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise'
  },
  {
    lang: 'frontend',
    keyword: 'debounce',
    category: '交互',
    summary: '在事件停止触发一段时间后才执行，常用于搜索框输入。',
    code: `function debounce(fn, wait = 200) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const search = debounce((q) => console.log("search", q));`,
    note: '组件卸载时要 clearTimeout；请求场景可以配合 AbortController 取消上一次请求。',
    docs: 'https://developer.mozilla.org/en-US/docs/Web/API/setTimeout'
  },
  {
    lang: 'frontend',
    keyword: 'fetch',
    category: '网络',
    summary: '浏览器内置的 HTTP 请求 API，返回 Promise<Response>。',
    code: `async function load(q) {
  const res = await fetch("/api/search?q=" + encodeURIComponent(q));
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}`,
    note: '记得 encodeURIComponent、设置超时/取消（AbortController），并把错误提示给用户。',
    docs: 'https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API'
  },

  /* -------------------------------- MySQL ----------------------------- */
  {
    lang: 'mysql',
    keyword: 'LIKE',
    category: '查询',
    summary: '用通配符做包含匹配，% 表示任意多个字符，_ 表示单个字符。',
    code: `SELECT id, title
FROM articles
WHERE title LIKE CONCAT('%', ?, '%')
   OR body  LIKE CONCAT('%', ?, '%')
LIMIT 50;`,
    note: '前置 % 会让普通 B+Tree 索引失效；小表/后台查询可接受，大表应改用全文索引。',
    docs: 'https://dev.mysql.com/doc/refman/8.0/en/string-comparison-functions.html#operator_like'
  },
  {
    lang: 'mysql',
    keyword: 'FULLTEXT',
    category: '全文检索',
    summary: 'MySQL 的全文索引，适合文章标题/正文的关键词搜索和相关性排序。',
    code: `ALTER TABLE articles
  ADD FULLTEXT INDEX ft_articles (title, body);

SELECT id, title,
       MATCH(title, body) AGAINST (? IN NATURAL LANGUAGE MODE) AS score
FROM articles
WHERE MATCH(title, body) AGAINST (? IN NATURAL LANGUAGE MODE)
ORDER BY score DESC
LIMIT 50;`,
    note: 'InnoDB 支持；中文要加 WITH PARSER ngram（默认 ngram_token_size=2）；短词/停用词可能搜不到。',
    docs: 'https://dev.mysql.com/doc/refman/8.0/en/fulltext-search.html'
  },
  {
    lang: 'mysql',
    keyword: 'JOIN',
    category: '关联查询',
    summary: '把多张表按关联条件组合起来，常见有 INNER/LEFT/RIGHT JOIN。',
    code: `SELECT a.id, a.title, t.name
FROM articles a
JOIN article_tags at ON at.article_id = a.id
JOIN tags t ON t.id = at.tag_id
WHERE t.name = ?
LIMIT 50;`,
    note: '关联列要有索引；多对多用中间表；先用 EXPLAIN 看驱动表和扫描行数。',
    docs: 'https://dev.mysql.com/doc/refman/8.0/en/join.html'
  },
  {
    lang: 'mysql',
    keyword: 'EXPLAIN',
    category: '性能',
    summary: '查看 SQL 的执行计划，判断有没有走索引、扫描多少行。',
    code: `EXPLAIN ANALYZE
SELECT id, title
FROM articles
WHERE title LIKE '%mysql%'
LIMIT 50;`,
    note: '重点看 type、key、rows、filtered；type=ALL 是全表扫描，Using filesort/Using temporary 要留意。',
    docs: 'https://dev.mysql.com/doc/refman/8.0/en/explain.html'
  },
  {
    lang: 'mysql',
    keyword: 'transaction',
    category: '事务',
    summary: '一组要么全部成功、要么全部回滚的 SQL 操作，InnoDB 支持事务。',
    code: `START TRANSACTION;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;`,
    note: '转账要先检查余额并保持加锁顺序一致；异常分支记得 ROLLBACK，避免长事务。',
    docs: 'https://dev.mysql.com/doc/refman/8.0/en/commit.html'
  },

  /* --------------------------------- Go ------------------------------- */
  {
    lang: 'go',
    keyword: 'goroutine',
    category: '并发',
    summary: '用 go 关键字启动的轻量级协程，由 Go 运行时调度。',
    code: `package main

import (
    "fmt"
    "sync"
)

func main() {
    var wg sync.WaitGroup
    wg.Add(1)
    go func() {
        defer wg.Done()
        fmt.Println("hello")
    }()
    wg.Wait()
}`,
    note: '主协程退出会带走所有 goroutine；要用 WaitGroup、channel 或 context 管理生命周期。',
    docs: 'https://go.dev/tour/concurrency/1'
  },
  {
    lang: 'go',
    keyword: 'channel',
    category: '并发',
    summary: 'goroutine 之间通信的管道，支持无缓冲和有缓冲两种。',
    code: `package main

import "fmt"

func main() {
    ch := make(chan int, 1)
    ch <- 42
    fmt.Println(<-ch)
    close(ch)
}`,
    note: '无缓冲 channel 会同步阻塞；只由发送方 close；不要向已关闭的 channel 发送数据。',
    docs: 'https://go.dev/tour/concurrency/2'
  },
  {
    lang: 'go',
    keyword: 'defer',
    category: '语法',
    summary: '把函数调用推迟到当前函数返回前执行，后进先出。',
    code: `package main

import "fmt"

func main() {
    defer fmt.Println("done")
    fmt.Println("work")
}`,
    note: '常用于解锁、关闭文件、recover；defer 的参数在注册时求值，不是执行时。',
    docs: 'https://go.dev/tour/flowcontrol/12'
  },
  {
    lang: 'go',
    keyword: 'interface',
    category: '抽象',
    summary: '一组方法签名的集合，类型只要实现了这些方法就自动满足接口。',
    code: `package main

import "fmt"

type Greeter interface {
    Greet() string
}

type English struct{}

func (English) Greet() string { return "hello" }

func main() {
    var g Greeter = English{}
    fmt.Println(g.Greet())
}`,
    note: 'Go 是隐式实现；接口应尽量小；any 是空接口，取回具体类型要用类型断言或 type switch。',
    docs: 'https://go.dev/tour/methods/9'
  },
  {
    lang: 'go',
    keyword: 'context',
    category: '并发',
    summary: '在调用链上传递取消信号、超时和请求范围的值。',
    code: `package main

import (
    "context"
    "fmt"
    "time"
)

func main() {
    ctx, cancel := context.WithTimeout(context.Background(), time.Second)
    defer cancel()
    select {
    case <-time.After(2 * time.Second):
        fmt.Println("slow")
    case <-ctx.Done():
        fmt.Println("timeout")
    }
}`,
    note: 'context 一般作为函数第一个参数；不要存进结构体；WithTimeout/WithCancel 的 cancel 必须调用。',
    docs: 'https://pkg.go.dev/context'
  }
];
