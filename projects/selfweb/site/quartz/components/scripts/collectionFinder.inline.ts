function setupCollectionFinders() {
  const finders = document.querySelectorAll<HTMLElement>(".collection-finder")

  for (const finder of finders) {
    if (finder.dataset.finderReady === "true") continue
    finder.dataset.finderReady = "true"

    const pageSize = Number.parseInt(finder.dataset.pageSize ?? "12", 10) || 12
    const cardSurface = finder.querySelector<HTMLElement>('[data-result-surface="cards"]')
    const items = Array.from(
      cardSurface
        ? cardSurface.querySelectorAll<HTMLElement>(
            ".collection-linked-item, .collection-result-item",
          )
        : finder.querySelectorAll<HTMLElement>(".collection-result-item"),
    )
    const linkedItems = Array.from(
      finder.querySelectorAll<HTMLElement>(".collection-linked-item"),
    ).filter((item) => !items.includes(item))
    const pagination = finder.querySelector<HTMLElement>(".collection-pagination")
    const resultCount = finder.querySelector<HTMLElement>("[data-result-count]")
    const pageStatus = finder.querySelector<HTMLElement>("[data-page-status]")
    const emptyState = finder.querySelector<HTMLElement>("[data-empty-state]")
    let currentPage = 1

    const getActiveFilters = () => {
      const filters = new Map<string, string>()
      const activeButtons = finder.querySelectorAll<HTMLButtonElement>(
        ".collection-filter-chip.is-active",
      )

      for (const button of activeButtons) {
        const field = button.dataset.filterField
        const value = button.dataset.filterValue
        if (!field || !value || value === "all") continue
        filters.set(field, value)
      }

      return filters
    }

    const itemMatches = (item: HTMLElement, filters: Map<string, string>) => {
      for (const [field, value] of filters) {
        if (field === "category" && item.dataset.categories) {
          if (!item.dataset.categories.split(/\s+/).includes(value)) return false
          continue
        }

        if (item.dataset[field] !== value) return false
      }

      return true
    }

    const render = () => {
      const filters = getActiveFilters()
      const matched = items.filter((item) => itemMatches(item, filters))
      const totalPages = Math.max(1, Math.ceil(matched.length / pageSize))
      currentPage = Math.min(currentPage, totalPages)
      const start = (currentPage - 1) * pageSize
      const visible = new Set(matched.slice(start, start + pageSize))

      for (const item of items) {
        item.hidden = !visible.has(item)
      }
      for (const linkedItem of linkedItems) {
        const itemId = linkedItem.dataset.itemId
        const linkedVisible = itemId
          ? Array.from(visible).some((item) => item.dataset.itemId === itemId)
          : false
        linkedItem.hidden = !linkedVisible
      }

      if (resultCount) {
        resultCount.textContent = `${matched.length} 项`
      }
      if (pageStatus) {
        pageStatus.textContent =
          matched.length > pageSize ? `第 ${currentPage} / ${totalPages} 页` : ""
      }
      if (emptyState) {
        emptyState.hidden = matched.length > 0
      }
      if (!pagination) return

      pagination.innerHTML = ""
      if (matched.length <= pageSize) return

      const createButton = (label: string, page: number, active = false) => {
        const button = document.createElement("button")
        button.type = "button"
        button.textContent = label
        button.className = active ? "is-active" : ""
        button.addEventListener("click", () => {
          currentPage = page
          render()
          finder.scrollIntoView({ block: "start", behavior: "smooth" })
        })
        return button
      }

      pagination.append(createButton("上一页", Math.max(1, currentPage - 1)))
      for (let page = 1; page <= totalPages; page++) {
        pagination.append(createButton(String(page), page, page === currentPage))
      }
      pagination.append(createButton("下一页", Math.min(totalPages, currentPage + 1)))
    }

    finder.addEventListener("click", (event) => {
      const button = (event.target as Element | null)?.closest<HTMLButtonElement>(
        ".collection-filter-chip",
      )
      if (!button) return

      const field = button.dataset.filterField
      if (!field) return

      const group = button.closest(".collection-filter-row")
      const scopedButtons = group
        ? group.querySelectorAll<HTMLButtonElement>(".collection-filter-chip")
        : finder.querySelectorAll<HTMLButtonElement>(
            `.collection-filter-chip[data-filter-field="${field}"]`,
          )

      for (const scopedButton of scopedButtons) {
        scopedButton.classList.toggle("is-active", scopedButton === button)
      }

      currentPage = 1
      render()
    })

    render()
  }
}

document.addEventListener("nav", setupCollectionFinders)
document.addEventListener("render", setupCollectionFinders)
