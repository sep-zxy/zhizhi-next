// P16/P17 shared frame styles. String form keeps Node component tests loader-free.
export default String.raw`
@charset "UTF-8";
body:has(.page[data-frame=public-wiki]) {
  background: #fff;
  color: #293d4b;
}

.page[data-frame=public-wiki] {
  max-width: 1600px;
  margin: 0 auto;
  padding: 0;
  --secondary: #276e73;
  --tertiary: #276e73;
  --light: #fff;
  --dark: #203344;
  --darkgray: #435261;
  --lightgray: #dfe7e9;
}

.page[data-frame=public-wiki] > #quartz-body {
  display: block;
  padding: 0;
}

[data-frame=public-wiki] a {
  color: #276e73;
  font-weight: 500;
  background: none;
  text-decoration: none;
}
[data-frame=public-wiki] a:hover {
  text-decoration: underline;
}
[data-frame=public-wiki] a:focus-visible,
[data-frame=public-wiki] button:focus-visible {
  outline: 3px solid #39979b;
  outline-offset: 3px;
}
[data-frame=public-wiki] h1,
[data-frame=public-wiki] h2,
[data-frame=public-wiki] h3 {
  color: #243c4b;
  font-family: "Noto Serif CJK SC", "Songti SC", "SimSun", serif;
  letter-spacing: 0.015em;
}
[data-frame=public-wiki] h1 {
  font-size: 2.3rem;
  margin: 0 0 0.75rem;
  line-height: 1.4;
}
[data-frame=public-wiki] h2 {
  font-size: 1.4rem;
  margin: 1.6rem 0 1rem;
}
[data-frame=public-wiki] h3 {
  font-size: 1.18rem;
  margin: 0 0 0.7rem;
}
[data-frame=public-wiki] small {
  font-family: var(--bodyFont);
  font-weight: normal;
  font-size: 0.83rem;
  color: #73808a;
}
[data-frame=public-wiki] p,
[data-frame=public-wiki] li {
  line-height: 1.85;
}
[data-frame=public-wiki] .public-header {
  display: flex;
  align-items: center;
  gap: 3rem;
  height: 82px;
  padding: 0 2rem;
  border-bottom: 1px solid #dfe7e9;
}
[data-frame=public-wiki] .public-brand {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  white-space: nowrap;
  font-size: 1.35rem;
  font-weight: 700;
  color: #243c4b;
}
[data-frame=public-wiki] .public-brand > span {
  color: #276e73;
  font-size: 2.3rem;
}
[data-frame=public-wiki] .public-brand small {
  border-left: 1px solid #ccd8db;
  padding-left: 1rem;
  margin-left: 0.3rem;
  font-size: 0.95rem;
}
[data-frame=public-wiki] .public-search {
  flex: 1;
  max-width: 600px;
}
[data-frame=public-wiki] .public-search .search {
  max-width: none;
  min-width: 0;
  width: 100%;
}
[data-frame=public-wiki] .public-search .search-button {
  height: 42px;
  border: 1px solid #d1dcdf;
  background: #f6f9fa;
  border-radius: 5px;
}
[data-frame=public-wiki] .public-search .search-button p {
  font-size: 0;
}
[data-frame=public-wiki] .public-search .search-button p:after {
  content: "搜索条目、概念与技术栈";
  font-size: 0.95rem;
  color: #78858e;
}
[data-frame=public-wiki] .public-about-link {
  margin-left: auto;
  font-size: 0.9rem;
  white-space: nowrap;
}
[data-frame=public-wiki] .public-trail {
  display: flex;
  justify-content: space-between;
  padding: 0.6rem 2rem;
  border-bottom: 1px solid #e3e9ec;
  background: #fafcfc;
  font-size: 0.85rem;
  color: #78858e;
}
[data-frame=public-wiki] .public-layout {
  display: grid;
  grid-template-columns: 235px minmax(0, 1fr) 310px;
  min-height: calc(100vh - 180px);
}
[data-frame=public-wiki] .public-nav {
  border-right: 1px solid #e0e7e9;
  background: #f8fafb;
  padding: 1.2rem 1.2rem 2rem;
}
[data-frame=public-wiki] .public-nav h2 {
  font-size: 0.95rem;
  color: #76858e;
  font-family: var(--bodyFont);
  font-weight: 500;
  margin: 1rem 0.6rem 0.8rem;
}
[data-frame=public-wiki] .public-nav h2:not(:first-child) {
  border-top: 1px solid #dce5e7;
  padding-top: 1.6rem;
  margin-top: 1.8rem;
}
[data-frame=public-wiki] .public-nav a {
  display: block;
  padding: 0.65rem 0.7rem;
  color: #405563;
  border-radius: 4px;
  font-size: 0.95rem;
  overflow-wrap: anywhere;
}
[data-frame=public-wiki] .public-nav a[aria-current] {
  background: #e6f1f0;
  color: #276e73;
  font-weight: 600;
  border-left: 3px solid #39858a;
}
[data-frame=public-wiki] .public-readonly {
  color: #87959c;
  font-size: 0.8rem;
  margin: 3rem 0.7rem 0;
}
[data-frame=public-wiki] .public-main {
  min-width: 0;
  padding: 2.3rem 2.6rem;
}
[data-frame=public-wiki] .public-subtitle {
  font-size: 1.05rem;
  color: #647580;
  margin: 0 0 0.5rem;
}
[data-frame=public-wiki] .public-intro {
  color: #687984;
  padding-bottom: 1.6rem;
  border-bottom: 1px solid #dce4e7;
  margin-top: 0.5rem;
}
[data-frame=public-wiki] .public-categories {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 2.3rem;
  row-gap: 1.5rem;
}
[data-frame=public-wiki] .public-categories section {
  border-top: 1px solid #d7e1e3;
  padding-top: 1rem;
  scroll-margin-top: 1rem;
}
[data-frame=public-wiki] .public-categories h3 small {
  float: right;
  padding-top: 0.2rem;
}
[data-frame=public-wiki] .public-categories ul {
  padding-left: 1.1rem;
  margin: 0.5rem 0 0;
}
[data-frame=public-wiki] .public-categories li {
  padding: 0.18rem 0;
  font-size: 0.95rem;
}
[data-frame=public-wiki] .public-entry-row {
  padding: 1rem 0;
  border-top: 1px solid #e0e7e9;
}
[data-frame=public-wiki] .public-entry-row > a {
  font-size: 1.04rem;
  font-weight: 600;
}
[data-frame=public-wiki] .public-entry-row p {
  margin: 0.35rem 0 0 1.65rem;
  font-size: 0.9rem;
  color: #72808a;
}
[data-frame=public-wiki] .public-empty {
  background: #f5f9f9;
  border-left: 3px solid #9eb8ba;
  padding: 1rem;
  color: #64767e;
}
[data-frame=public-wiki] .public-rail {
  min-width: 0;
  padding: 2.6rem 2rem 2rem 0.8rem;
}
[data-frame=public-wiki] .public-rail section {
  margin-bottom: 2.2rem;
}
[data-frame=public-wiki] .public-rail h2,
[data-frame=public-wiki] .public-rail h3 {
  font-size: 1.12rem;
  border-bottom: 1px solid #dce4e7;
  padding-bottom: 0.8rem;
  margin: 0 0 1rem;
}
[data-frame=public-wiki] .public-rail p {
  font-size: 0.88rem;
  color: #697b86;
}
[data-frame=public-wiki] .public-revision {
  border-left: 1px solid #d3e3e4;
  padding: 0 0 1.3rem 1rem;
  position: relative;
}
[data-frame=public-wiki] .public-revision:before {
  content: "";
  position: absolute;
  left: -4px;
  top: 0.45rem;
  width: 7px;
  height: 7px;
  background: #3f8b8d;
  border-radius: 50%;
}
[data-frame=public-wiki] .public-revision time {
  display: block;
  font-size: 0.8rem;
  color: #7f8c94;
  margin-bottom: 0.35rem;
}
[data-frame=public-wiki] .public-revision a {
  display: block;
  font-size: 0.94rem;
}
[data-frame=public-wiki] .public-revision p {
  margin: 0.35rem 0 0;
}
[data-frame=public-wiki] .public-article-path {
  font-size: 0.85rem;
  color: #7c8b93;
  margin-bottom: 1.1rem;
}
[data-frame=public-wiki] .public-meta {
  font-size: 0.82rem;
  color: #7b8992;
  margin-bottom: 1.6rem;
}
[data-frame=public-wiki] article {
  font-size: 1rem;
  line-height: 1.9;
}
[data-frame=public-wiki] article h1 {
  font-size: 1.65rem;
}
[data-frame=public-wiki] article h2 {
  padding-top: 1.2rem;
  border-top: 1px solid #dce4e7;
}
[data-frame=public-wiki] article blockquote {
  border-left: 3px solid #4d9294;
  background: #f0f7f6;
  padding: 0.6rem 1.2rem;
  margin: 1.5rem 0;
  border-radius: 0;
}
[data-frame=public-wiki] .public-footer {
  border-top: 1px solid #dde5e8;
  padding: 1rem 2rem;
  color: #7b8992;
  font-size: 0.8rem;
}
[data-frame=public-wiki] .public-footer span {
  float: right;
}
[data-frame=public-wiki] .public-skip {
  position: absolute;
  left: -10000px;
}
[data-frame=public-wiki] .public-skip:focus {
  left: 1rem;
  top: 0.5rem;
  z-index: 100;
  padding: 0.5rem;
  background: #fff;
}
[data-frame=public-wiki] #about {
  margin-top: 2.4rem;
}
[data-frame=public-wiki] .toc {
  max-height: 55vh;
}

@media (max-width: 1100px) {
  [data-frame=public-wiki] .public-layout {
    grid-template-columns: 195px minmax(0, 1fr);
  }
  [data-frame=public-wiki] .public-rail {
    grid-column: 2;
    padding: 0 2rem 2rem;
  }
  [data-frame=public-wiki] .public-main {
    padding: 2rem;
  }
  [data-frame=public-wiki] .public-header {
    gap: 1.4rem;
  }
  [data-frame=public-wiki] .public-brand small {
    display: none;
  }
}
@media (max-width: 650px) {
  [data-frame=public-wiki] .public-header {
    flex-wrap: wrap;
    height: auto;
    padding: 0.8rem 1rem;
    gap: 0.7rem;
  }
  [data-frame=public-wiki] .public-brand {
    font-size: 1.15rem;
  }
  [data-frame=public-wiki] .public-search {
    order: 3;
    flex-basis: 100%;
    max-width: none;
  }
  [data-frame=public-wiki] .public-layout {
    display: block;
  }
  [data-frame=public-wiki] .public-nav {
    display: flex;
    overflow-x: auto;
    gap: 0.5rem;
    padding: 0.5rem;
    border-bottom: 1px solid #dce4e7;
  }
  [data-frame=public-wiki] .public-nav h2,
  [data-frame=public-wiki] .public-readonly {
    display: none;
  }
  [data-frame=public-wiki] .public-nav a {
    flex-shrink: 0;
    padding: 0.5rem;
  }
  [data-frame=public-wiki] .public-main,
  [data-frame=public-wiki] .public-rail {
    padding: 1.3rem;
  }
  [data-frame=public-wiki] h1 {
    font-size: 1.75rem;
  }
  [data-frame=public-wiki] .public-categories {
    grid-template-columns: 1fr;
  }
  [data-frame=public-wiki] .public-trail {
    padding: 0.6rem 1rem;
  }
  [data-frame=public-wiki] .public-trail span {
    display: none;
  }
  [data-frame=public-wiki] .public-footer {
    padding: 1rem;
  }
  [data-frame=public-wiki] .public-footer span {
    display: block;
    float: none;
    margin-top: 0.5rem;
  }
}
`
