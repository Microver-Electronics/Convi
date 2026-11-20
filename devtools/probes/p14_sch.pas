procedure LogPins(C: Variant; Tag: String);
var It, P: Variant;
begin
  It := C.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePin));
  P := It.FirstSchObject;
  while P <> Nil do
  begin
    L(Tag + ' pin ' + P.Designator + ' loc=' + IntToStr(CoordToMils(P.Location.X)) + ',' + IntToStr(CoordToMils(P.Location.Y)) + ' orient=' + IntToStr(P.Orientation) + ' len=' + IntToStr(CoordToMils(P.PinLength)));
    P := It.NextSchObject;
  end;
  C.SchIterator_Destroy(It);
end;

function MakeComp(Doc: Variant; X, Y: Integer; Orient: Integer; Mir: Boolean; Tag: String): Variant;
var C, P: Variant;
begin
  C := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  C.CurrentPartID := 1;
  C.DisplayMode := 0;
  C.LibReference := 'PROBE';
  C.Location := Point(MilsToCoord(X), MilsToCoord(Y));
  Doc.RegisterSchObjectInContainer(C);
  P := SchServer.SchObjectFactory(ePin, eCreate_Default);
  P.Location := Point(MilsToCoord(X + 100), MilsToCoord(Y + 200));
  P.Orientation := eRotate0;
  P.PinLength := MilsToCoord(300);
  P.Designator := '1';
  P.Name := 'A';
  P.OwnerPartId := 1;
  C.AddSchObject(P);
  C.Designator.Text := Tag;
  LogPins(C, Tag + ' before');
  if Mir then
  begin
    try
      C.Mirror(C.Location);
    except
      L('Mirror(loc) failed: ' + ExceptionMessage);
    end;
  end;
  if Orient > 0 then C.RotateBy90(C.Location, Orient);
  LogPins(C, Tag + ' after o=' + IntToStr(Orient) + ' m=' + BoolToStr(Mir, True) + ' compLoc=' + IntToStr(CoordToMils(C.Location.X)) + ',' + IntToStr(CoordToMils(C.Location.Y)) + ' state o=' + IntToStr(C.Orientation) + ' mir=' + BoolToStr(C.IsMirrored, True));
  Result := C;
end;

procedure Body;
var Doc, SchDoc, C: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('SCH');
  SchDoc := SchServer.GetCurrentSchDocument;
  L('schdoc ok: ' + BoolToStr(SchDoc <> Nil, True));
  C := MakeComp(SchDoc, 1000, 1000, eRotate0, True, 'M0');
  C := MakeComp(SchDoc, 2000, 1000, eRotate90, False, 'R90');
  C := MakeComp(SchDoc, 3000, 1000, eRotate90, True, 'R90M');
  C := MakeComp(SchDoc, 4000, 1000, eRotate180, False, 'R180');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p14b.SchDoc');
  Doc.DoFileSave('');
  L('saved');
end;
