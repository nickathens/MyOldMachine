function startReel(slides) {
  let index = 0;
  const DWELL = 10000;
  function next() {
    index = (index + 1) % slides.length;
    console.log('now showing ' + slides[index].title);
    setTimeout(next, DWELL);
  }
  setTimeout(next, DWELL);
}
startReel([{ title: 'First film' }, { title: 'Second film' }]);
